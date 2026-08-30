#!/usr/bin/env python3
"""Deterministic bootstrap protocol for persistent design discussions."""

from __future__ import annotations

import fcntl
import base64
import hashlib
import json
import os
from pathlib import Path
import re
import stat
import subprocess
import sys
import tempfile
import time
import uuid
from typing import Any

sys.path.insert(0, str(Path(__file__).parent))
from discussion_core import OperationRegistry, RequestContext


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
        "git_probe_failed": "无法可靠探测 Git worktree，已停止且不会创建备用协调状态。",
        "idempotency_conflict": "同一幂等键已用于不同的初始化参数。",
        "initialization_conflict": "初始化目标已存在，已停止且未覆盖原内容。",
        "initialization_failed": "持久化讨论初始化失败，已停止并回滚本次新增状态。",
        "initialization_verification_failed": "初始化后的权威状态回读验证失败。",
        "injected_failure": "测试故障注入已在指定边界停止操作。",
        "active_question_conflict": "当前话题已经有一个用户可见问题。",
        "coordination_busy": "讨论账本协调暂时繁忙，请稍后重试。",
        "discussion_identity_conflict": "讨论项目、树或话题身份不匹配。",
        "document_lease_invalid": "文档租约凭证无效、过期或不属于当前写入者。",
        "document_lease_release_unverified": "文档租约释放状态无法验证。",
        "document_ownership_conflict": "调用者不是当前话题文档写入所有者。",
        "document_write_before_conflict": "话题文档已偏离待写入载荷的准备基线。",
        "document_write_payload_damaged": "待写入载荷缺失或摘要损坏。",
        "document_write_orphan_conflict": "孤立载荷与精确重放请求的类型或摘要不匹配。",
        "document_write_reconciliation_required": "存在已确认但未完成的文档写入，必须先恢复协调。",
        "document_write_state_conflict": "待写入记录当前状态不允许此操作。",
        "document_write_verification_failed": "文档写入后的字节校验失败。",
        "checkpoint_active_source_ack_required": "检查点修复会影响活动实现来源，必须重新确认。",
        "checkpoint_base_invalid": "检查点基础 Git 引用无效。",
        "checkpoint_changed_draft": "当前草案已偏离冻结字节，必须取消或替代旧检查点意图。",
        "checkpoint_gc_confirmation_mismatch": "检查点清理确认与当前候选或账本修订不一致。",
        "checkpoint_gc_reconciliation_required": "存在结果不确定的检查点清理，必须先完成对账。",
        "checkpoint_history_ambiguous": "Git 历史中存在多个完全匹配的检查点候选。",
        "checkpoint_history_mismatch": "检查点提交、路径、树、blob 或摘要无法完全验证。",
        "checkpoint_identity_conflict": "检查点身份、状态或幂等意图冲突。",
        "checkpoint_reconciliation_required": "存在结果不确定的旧检查点，必须先完成对账。",
        "checkpoint_repair_not_unique": "检查点修复没有找到唯一且完全验证的替代提交。",
        "checkpoint_snapshot_corrupt": "非 Git 检查点快照缺失或内容寻址对象不匹配。",
        "repository_coordination_lease_invalid": "Git 检查点协调租约无效或不属于当前操作者。",
        "impact_state_conflict": "该决定影响当前不能按请求处理。",
        "handoff_attempt_state_conflict": "交接尝试当前状态不允许此操作。",
        "handoff_identity_conflict": "交接身份或验证载荷不匹配。",
        "handoff_late_arrival": "迟到的外部创建结果属于已撤销尝试，不能绑定。",
        "handoff_next_turn_required": "交接首轮只能验收，必须等待下一轮再开始实质讨论。",
        "handoff_payload_too_large": "交接工作快照或完整载荷超过大小限制。",
        "handoff_reconciliation_required": "外部创建结果未知，必须先对账或由用户明确强制重试。",
        "child_result_state_conflict": "子话题结果声明当前状态不允许此操作。",
        "ledger_revision_conflict": "讨论账本修订已变化，请重读后重试。",
        "orphaned_document_write": "发现孤立、缺失或未归属的待写入载荷。",
        "question_state_conflict": "该问题当前不是可恢复、调整或失效的挂起状态。",
        "record_not_found": "请求引用的权威记录不存在或不唯一。",
        "record_revision_conflict": "话题记录修订已变化，请重读后重试。",
        "reconciliation_conflict": "待写入检查点与当前文档或租约状态冲突。",
        "invalid_entry_mode": "当前入口不是明确的持久化 0讨论 触发。",
        "invalid_json": "标准输入必须只包含一个有效 JSON 值。",
        "invalid_project_path": "项目路径必须是已存在且规范化的绝对目录。",
        "invalid_request": "请求不符合 bootstrap 类型化接口。",
        "internal_error": "讨论协议遇到内部错误，已停止并保留最后的权威状态。",
        "invalid_root_slug": "根话题 slug 格式无效。",
        "invalid_storage_path": "持久化路径包含不安全或无效的组件。",
        "state_corrupt": "持久化讨论权威状态损坏或不完整。",
        "unsupported_operation": "讨论协议操作不受支持。",
        "unsupported_protocol_version": "协议版本不受支持。",
        "invalid_phase_route": "阶段路由不在允许的前向转换集合中。",
        "phase_route_conflict": "阶段路由与当前话题阶段不匹配。",
        "phase_reopen_review_required": "返回 0 必须显式 reopen 并完成受影响决定复核。",
        "phase_run_state_conflict": "Phase Run 当前状态不允许此操作。",
        "phase_attempt_state_conflict": "Phase Run 外部载体 attempt 当前状态不允许此操作。",
        "phase_identity_conflict": "Phase Run 身份或冻结意图不匹配。",
        "phase_source_drift": "来源检查点已变化，必须重新准备 Phase Run。",
        "phase_route_drift": "路由证据已变化，必须重新准备 Phase Run。",
        "phase_impact_drift": "影响状态已变化，必须重新准备 Phase Run。",
        "phase_coverage_drift": "覆盖范围已变化，必须重新准备 Phase Run。",
        "phase_dependency_drift": "依赖状态已变化，必须重新准备 Phase Run。",
        "phase_coordination_drift": "协调状态已变化，必须重新准备 Phase Run。",
        "phase_authorization_required": "该 Phase Run 操作需要来源话题授权。",
        "phase_carrier_claim_required": "专用阶段载体必须先验证来源并认领当前 attempt。",
        "phase_checkpoint_invalid": "阶段来源检查点不存在、未完成、已过期或身份不匹配。",
        "phase_requirement_incomplete": "1 到 3 的需求完整性门禁尚未全部满足。",
        "phase_flow_mode_invalid": "连续模式只能来自成功的 1拷问 footer。",
        "phase_completion_not_claimed": "Phase Run 尚未提交可验收的完成声明。",
        "phase_reconciliation_required": "Phase Run 结果未知，必须先完成对账。",
        "no_code_integration_invalid": "父级范围未被已完成且已吸收的子实现完整覆盖。",
        "context_not_initialized": "document_only 上下文尚未获得用户授权初始化本地协调状态。",
        "context_identity_conflict": "发现的讨论上下文身份存在强冲突。",
        "implementation_identity_conflict": "实现运行身份或冻结计划不匹配。",
        "implementation_parallelism_stale": "并行判定输入已变化，必须重新检查。",
        "supervision_receipt_invalid": "监督协议凭证无效、过期、重放或不属于当前话题。",
        "implementation_authority_mismatch": "调用方提供的活动实现与讨论账本权威状态不一致。",
        "source_refresh_impact_mismatch": "来源刷新影响与讨论账本权威决定/范围记录不一致。",
        "integration_receipt_stale": "集成凭证已偏离当前讨论来源、依赖或活动运行状态。",
        "execution_mode_frozen": "实现运行激活后执行模式不可切换。",
        "implementation_source_invalid": "实现来源必须是已提交且完整验证的 implementation-source 检查点。",
        "isolated_worktree_confirmation_required": "isolated worktree 需要对精确安排的显式确认。",
        "source_refresh_state_conflict": "来源刷新当前状态不允许此操作。",
        "archive_authority_invalid": "归档来源、实现、合并、提案或保留检查点未通过验证。",
        "archive_checkpoint_invalid": "归档保留检查点缺失、未完成或与冻结执行模式不匹配。",
        "topic_close_blocked": "归档已完成，但话题仍有协调事项，不能关闭。",
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
        "checkpoint_id": error.context.get("checkpoint_id"),
        "error": detail,
    }


def _unexpected_error_context(request: Any) -> dict[str, Any]:
    if not isinstance(request, dict):
        return {}
    context: dict[str, Any] = {}
    for request_key, response_key in (
        ("project_id", "project_id"),
        ("tree_id", "tree_id"),
        ("actor_topic_id", "topic_id"),
        ("topic_id", "topic_id"),
        ("checkpoint_id", "checkpoint_id"),
    ):
        value = request.get(request_key)
        if (
            response_key not in context
            and isinstance(value, str)
            and value
            and len(value.encode("utf-8")) <= 512
        ):
            context[response_key] = value
    return context


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
    environment = dict(os.environ)
    environment["LC_ALL"] = "C"
    try:
        membership = subprocess.run(
            ["git", "rev-parse", "--is-inside-work-tree"],
            cwd=project,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            env=environment,
        )
    except OSError as error:
        raise ProtocolError(
            "git_probe_failed", "Git worktree membership could not be probed"
        ) from error
    if membership.returncode != 0:
        if "not a git repository" in membership.stderr.casefold():
            return None
        raise ProtocolError(
            "git_probe_failed", "Git worktree membership probe failed closed"
        )
    membership_value = membership.stdout.strip().casefold()
    if membership_value == "false":
        return None
    if membership_value != "true":
        raise ProtocolError(
            "git_probe_failed", "Git worktree membership probe returned an invalid result"
        )
    try:
        completed = subprocess.run(
            ["git", "rev-parse", "--path-format=absolute", "--git-common-dir"],
            cwd=project,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            env=environment,
        )
    except OSError as error:
        raise ProtocolError(
            "git_probe_failed", "Git common directory could not be probed"
        ) from error
    if completed.returncode != 0:
        raise ProtocolError("git_probe_failed", "Git common directory probe failed closed")
    common_dir_text = completed.stdout.strip()
    path = Path(common_dir_text)
    if not common_dir_text or not path.is_absolute():
        raise ProtocolError(
            "git_identity_invalid", "Git common directory is empty or not absolute"
        )
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


def _parse_yaml_scalar(value: str, label: str) -> str | int | bool | None:
    if value == "null":
        return None
    if value == "true":
        return True
    if value == "false":
        return False
    if re.fullmatch(r"0|[1-9][0-9]*", value):
        return int(value)
    if value.startswith('"'):
        try:
            decoded = json.loads(value)
        except json.JSONDecodeError as error:
            raise ProtocolError("state_corrupt", f"{label} has an invalid quoted scalar") from error
        if not isinstance(decoded, str):
            raise ProtocolError("state_corrupt", f"{label} must decode to a string")
        return decoded
    raise ProtocolError("state_corrupt", f"{label} uses an unsupported YAML scalar")


def _parse_record_section(section: str, label: str) -> list[dict[str, Any]]:
    lines = section.strip().splitlines()
    if len(lines) < 3 or lines[:2] != ["```yaml", "records:"] or lines[-1] != "```":
        raise ProtocolError("state_corrupt", f"{label} has invalid YAML framing")
    content = lines[2:-1]
    if content == ["  []"]:
        return []
    records: list[dict[str, Any]] = []
    current: dict[str, Any] | None = None
    for line in content:
        if line.startswith("  - "):
            current = {}
            records.append(current)
            field_line = line[4:]
        elif line.startswith("    ") and current is not None:
            field_line = line[4:]
        else:
            raise ProtocolError("state_corrupt", f"{label} contains invalid indentation")
        if ": " not in field_line:
            raise ProtocolError("state_corrupt", f"{label} contains a malformed field")
        key, raw_value = field_line.split(": ", 1)
        if not re.fullmatch(r"[a-z][a-z0-9_]*", key) or key in current:
            raise ProtocolError("state_corrupt", f"{label} contains an invalid or duplicate key")
        current[key] = _parse_yaml_scalar(raw_value, f"{label}.{key}")
    return records


def _require_exact_record(
    sections: dict[str, str], section_name: str, expected: dict[str, Any]
) -> None:
    records = _parse_record_section(sections[section_name], section_name)
    if records != [expected]:
        raise ProtocolError("state_corrupt", f"ledger {section_name!r} record is invalid")


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
        event_records = _parse_record_section(sections["Recent Events"], "Recent Events")
        if len(event_records) != 1:
            raise ProtocolError("state_corrupt", "bootstrap must have exactly one recent event")
        event_record = event_records[0]
        if event_record.get("idempotency_key") != idempotency_key:
            raise ProtocolError(
                "discussion_already_initialized",
                "the project already has a different persistent discussion root",
                context=context,
            )
        if event_record.get("request_fingerprint") != request_fingerprint:
            raise ProtocolError(
                "idempotency_conflict",
                "idempotency key was already used with different bootstrap parameters",
                context=context,
            )
        _require_exact_record(
            sections,
            "Current Topics",
            {
                "topic_id": manifest["topic_id"],
                "record_revision": 1,
                "root_slug": root_slug,
                "current_phase": 0,
                "phase_state": "active",
                "review_state": "unreviewed",
                "topic_state": "open",
                "topic_document_path": str(topic_path),
            },
        )
        _require_exact_record(
            sections,
            "Conversation Bindings",
            {
                "topic_id": manifest["topic_id"],
                "conversation_ref": conversation_ref,
                "binding_state": "active",
                "record_revision": 1,
            },
        )
        expected_event = {
            "event_id": "event-00000001",
            "event_type": "root-topic-bootstrapped",
            "ledger_revision": 1,
            "topic_id": manifest["topic_id"],
            "idempotency_key": idempotency_key,
            "request_fingerprint": request_fingerprint,
        }
        if event_record != expected_event:
            raise ProtocolError("state_corrupt", "bootstrap recent event is invalid")
        for empty_name in LEDGER_SECTION_NAMES:
            if empty_name in {"Current Topics", "Conversation Bindings", "Recent Events"}:
                continue
            if _parse_record_section(sections[empty_name], empty_name) != []:
                raise ProtocolError("state_corrupt", f"bootstrap section {empty_name!r} must be empty")
    except ProtocolError as error:
        error.context = {**context, **error.context}
        raise
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
    project = (
        request.project_path
        if isinstance(request, RequestContext) and request.project_path is not None
        else _validate_project_path(request["project_path"])
    )
    conversation_ref = (
        request.conversation_ref
        if isinstance(request, RequestContext) and request.conversation_ref is not None
        else _expect_string(request["conversation_ref"], "conversation_ref")
    )
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
        lock_stream = lock_path.open("a+b")
        try:
            timeout_text = os.environ.get("CODEX_DISCUSSION_TEST_LOCK_TIMEOUT_SECONDS")
            timeout_seconds = 5.0
            if timeout_text is not None:
                try:
                    timeout_seconds = min(5.0, max(0.05, float(timeout_text)))
                except ValueError:
                    timeout_seconds = 5.0
            _flock_with_timeout(lock_stream, timeout_seconds)
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


SUBSTANTIVE_MUTATIONS = {
    "confirm-decision",
    "set-active-question",
    "insert-idea",
    "resolve-inserted-idea",
    "change-direction",
    "resolve-impact",
}
IMPACT_ACTIONS = {"keep", "adjust", "replace", "discard"}
QUESTION_ACTIONS = {"resume", "adjust", "invalidate"}
HANDOFF_KINDS = {"child", "continuation"}
HANDOFF_WORK_SNAPSHOT_FIELDS = {
    "goal",
    "confirmed_decisions",
    "candidate_solution",
    "tentative_assumptions",
    "facts",
    "pending_questions",
}
HANDOFF_WORK_SNAPSHOT_MAX_BYTES = 16000
HANDOFF_PAYLOAD_MAX_BYTES = 32000
SHA256_RE = re.compile(r"[0-9a-f]{64}")

PHASE_ROUTES = {(0, 1), (0, 2), (1, 2), (1, 3), (2, 3), (3, 4)}
PHASE_RUN_STATES = {
    "prepared", "setup-pending", "ready", "active", "completion-claimed",
    "completion-pending", "completed", "blocked", "failed", "outcome-unknown",
    "cancelled", "superseded",
}
PHASE_ATTEMPT_STATES = {
    "setup-pending", "ready", "active", "completion-claimed", "completion-pending",
    "completed", "blocked", "failed", "outcome-unknown", "cancelled", "superseded",
}
PHASE_DRIFT_CODES = {
    "source": "phase_source_drift",
    "route": "phase_route_drift",
    "impact": "phase_impact_drift",
    "coverage": "phase_coverage_drift",
    "dependency": "phase_dependency_drift",
    "coordination": "phase_coordination_drift",
}
WRAPPER_CARRIER_ROUTES = {
    "current-problem-framing": {(0, 1)},
    "dedicated-grilling": {(0, 1)},
    "solution-designer": {(0, 2), (1, 2)},
    "guided-implementation": {(1, 3)},
}
DIRECT_IMPLEMENTATION_COMPLETENESS = {
    "scope", "behavior", "failures", "acceptance_conditions", "test_seam"
}
IMPLEMENTATION_PARALLELISM_DIMENSIONS = [
    "paths",
    "modules",
    "interfaces",
    "database_objects",
    "dependencies",
    "base_commit",
    "branch",
    "active_worktrees",
]

def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _atomic_replace(path: Path, data: bytes, *, mode: int = 0o600) -> None:
    temporary = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, mode)
    try:
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
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


def _flock_with_timeout(stream: Any, timeout_seconds: float = 5.0) -> None:
    deadline = time.monotonic() + timeout_seconds
    while True:
        try:
            fcntl.flock(stream.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            return
        except BlockingIOError as error:
            if time.monotonic() >= deadline:
                raise ProtocolError(
                    "coordination_busy",
                    "discussion ledger lock did not become available within five seconds",
                    retryable=True,
                ) from error
            time.sleep(0.05)


def _render_records_ledger(
    frontmatter: dict[str, str], records: dict[str, list[dict[str, Any]]]
) -> bytes:
    body_lines = ["# Design Discussion Ledger", ""]
    for name in LEDGER_SECTION_NAMES:
        body_lines.extend([f"## {name}", "", _yaml_record_block(records[name]), ""])
    body = "\n".join(body_lines)
    frontmatter_without_digest = (
        f"schema_version: {frontmatter['schema_version']}\n"
        f"project_id: {frontmatter['project_id']}\n"
        f"tree_id: {frontmatter['tree_id']}\n"
        f"ledger_revision: {frontmatter['ledger_revision']}\n"
        f"event_count: {frontmatter['event_count']}\n"
        f"project_manifest_path: {json.dumps(frontmatter['project_manifest_path'])}\n"
    )
    digest = _sha256((frontmatter_without_digest + body).encode("utf-8"))
    return (
        "---\n"
        + frontmatter_without_digest
        + f"content_digest: {digest}\n"
        + "---\n"
        + body
    ).encode("utf-8")


def _load_records(ledger_path: Path) -> tuple[dict[str, str], dict[str, list[dict[str, Any]]]]:
    frontmatter, text = _verify_ledger_digest(
        _require_regular_nosymlink(ledger_path, "ledger")
    )
    sections = _ledger_sections(text)
    return frontmatter, {
        name: _parse_record_section(sections[name], name) for name in LEDGER_SECTION_NAMES
    }


def _evolution_paths(
    request: dict[str, Any], *, query: bool = False, allow_tree_topic: bool = False
) -> tuple[Path, Path, Path, Path, str]:
    required = {
        "protocol_version",
        "operation",
        "project_path",
        "project_id",
        "tree_id",
        "actor_topic_id",
        "actor_conversation_ref",
    }
    if not query:
        required |= {
            "expected_ledger_revision",
            "expected_topic_revision",
            "idempotency_key",
        }
    if not required.issubset(request):
        raise ProtocolError(
            "invalid_request",
            f"request is missing fields: {sorted(required - set(request))!r}",
        )
    if isinstance(request, RequestContext):
        project = request.project_path
        project_id = request.project_id
        tree_id = request.tree_id
        topic_id = request.actor_topic_id
        owner_ref = request.actor_conversation_ref
        if None in {project, project_id, tree_id, topic_id, owner_ref}:
            raise ProtocolError("invalid_request", "request common envelope is incomplete")
    else:
        project = _validate_project_path(request["project_path"])
        project_id = _expect_string(request["project_id"], "project_id")
        tree_id = _expect_string(request["tree_id"], "tree_id")
        topic_id = _expect_string(request["actor_topic_id"], "actor_topic_id")
        owner_ref = _expect_string(
            request["actor_conversation_ref"], "actor_conversation_ref"
        )
    for label, value, kind in (
        ("project_id", project_id, "project"),
        ("tree_id", tree_id, "tree"),
        ("actor_topic_id", topic_id, "topic"),
    ):
        if not value.startswith(f"{kind}-") or not IDENTITY_RE.fullmatch(value):
            raise ProtocolError("discussion_identity_conflict", f"{label} is invalid")
    manifest_path = project / "docs" / "discussions" / ".codex-project.md"
    manifest = _parse_frontmatter(
        _require_regular_nosymlink(manifest_path, "project identity manifest"),
        "project identity manifest",
    )
    observed = (manifest.get("project_id"), manifest.get("tree_id"))
    if observed != (project_id, tree_id) or (
        not allow_tree_topic and manifest.get("topic_id") != topic_id
    ):
        raise ProtocolError(
            "discussion_identity_conflict",
            "request identity does not match the project identity manifest",
        )
    coordination_root, _ = _coordination_root(project)
    ledger_path = coordination_root / "projects" / project_id / "trees" / tree_id / "ledger.md"
    topic_path = project / "docs" / "discussions" / manifest["root_slug"] / "topic.md"
    lock_path = coordination_root / "locks" / _project_lock_name(project)
    return project, ledger_path, topic_path, lock_path, owner_ref


def _record_by_id(
    records: list[dict[str, Any]], field: str, value: str, label: str
) -> dict[str, Any]:
    matches = [record for record in records if record.get(field) == value]
    if len(matches) != 1:
        raise ProtocolError("record_not_found", f"{label} does not identify one record")
    return matches[0]


def _verify_topic_owner(
    records: dict[str, list[dict[str, Any]]],
    topic_id: str,
    owner_ref: str,
    *,
    allow_active_grilling: bool = False,
) -> None:
    active = [
        record
        for record in records["Conversation Bindings"]
        if record.get("topic_id") == topic_id and record.get("binding_state") == "active"
    ]
    if len(active) == 1 and active[0].get("conversation_ref") == owner_ref:
        return
    if not allow_active_grilling:
        raise ProtocolError(
            "document_ownership_conflict",
            "the caller is not the active document owner for this topic",
        )
    active_grilling_carriers = []
    for record in records["Phase Runs"]:
        if record.get("run_kind") != "phase-run" or record.get("state") != "active":
            continue
        data = _json_field(record, "data_json", "phase run")
        if (
            data.get("wrapper_integration") is True
            and data.get("carrier_kind") == "dedicated-grilling"
            and data.get("source_topic_id") == topic_id
        ):
            active_grilling_carriers.extend(
                attempt.get("carrier_ref")
                for attempt in data.get("attempts", [])
                if attempt.get("state") == "active" and attempt.get("claimed") is True
            )
    if active_grilling_carriers == [owner_ref]:
        return
    raise ProtocolError(
        "document_ownership_conflict",
        "the caller is not the active document owner for this topic",
    )


def _active_pending_write(records: dict[str, list[dict[str, Any]]]) -> dict[str, Any] | None:
    active = [
        record
        for record in records["Pending Document Writes"]
        if record.get("state") != "completed"
    ]
    if len(active) > 1:
        raise ProtocolError("state_corrupt", "more than one document write is active")
    return active[0] if active else None


def _json_field(record: dict[str, Any], field: str, label: str) -> Any:
    raw = record.get(field)
    if not isinstance(raw, str):
        raise ProtocolError("state_corrupt", f"{label}.{field} is missing")
    try:
        return json.loads(raw)
    except json.JSONDecodeError as error:
        raise ProtocolError("state_corrupt", f"{label}.{field} is invalid JSON") from error


def _topic_snapshot(records: dict[str, list[dict[str, Any]]], topic_id: str) -> dict[str, Any]:
    pending_items = [
        record for record in records["Pending Items"] if record.get("topic_id") == topic_id
    ]
    decisions = [
        _json_field(record, "data_json", "decision")
        for record in pending_items
        if record.get("item_kind") == "decision"
    ]
    questions = [
        _json_field(record, "data_json", "question")
        for record in pending_items
        if record.get("item_kind") == "question"
    ]
    ideas = [
        _json_field(record, "data_json", "idea")
        for record in pending_items
        if record.get("item_kind") == "idea"
    ]
    impacts = [
        _json_field(record, "data_json", "impact")
        for record in records["Impacts"]
        if record.get("topic_id") == topic_id
    ]
    return {
        "decisions": sorted(decisions, key=lambda item: item["decision_id"]),
        "questions": sorted(questions, key=lambda item: item["question_id"]),
        "ideas": sorted(ideas, key=lambda item: item["idea_id"]),
        "impacts": sorted(impacts, key=lambda item: item["impact_id"]),
    }


def _render_evolved_topic(
    *,
    project_id: str,
    tree_id: str,
    topic_id: str,
    root_slug: str,
    topic_revision: int,
    snapshot: dict[str, Any],
) -> bytes:
    decisions = snapshot["decisions"]
    active_decisions = [item for item in decisions if item.get("state") != "discarded"]
    decision_lines = [
        f"- `{item['decision_id']}` — {item['summary']}"
        + (f" Rationale: {item['rationale']}" if item.get("rationale") else "")
        for item in active_decisions
    ] or ["- None."]
    candidate_lines = [f"- {item['summary']}" for item in snapshot["ideas"]] or ["- None."]
    question_lines = [
        f"- `{item['question_id']}` [{item['state']}] {item['prompt']}"
        for item in snapshot["questions"]
        if item["state"] != "invalidated"
    ] or ["- None."]
    evolution_lines = []
    for item in decisions:
        evolution_lines.append(
            f"- `{item['decision_id']}`: {item.get('evolution', 'confirmed')}"
        )
    for impact in snapshot["impacts"]:
        evolution_lines.append(
            f"- `{impact['decision_id']}` impact `{impact['impact_id']}`: "
            f"{impact['state']}"
            + (f" ({impact['action']})" if impact.get("action") else "")
        )
    if not evolution_lines:
        evolution_lines = ["- None."]
    return (
        "---\n"
        "schema_version: 1\n"
        f"project_id: {project_id}\n"
        f"tree_id: {tree_id}\n"
        f"topic_id: {topic_id}\n"
        "parent_topic_id: null\n"
        f"topic_revision: {topic_revision}\n"
        "---\n"
        f"# {root_slug}\n\n"
        "## Confirmed Decisions\n\n"
        + "\n".join(decision_lines)
        + "\n\n## Candidate Solution\n\n"
        + "\n".join(candidate_lines)
        + "\n\n## Tentative Assumptions\n\n- None.\n\n"
        "## Facts\n\n- Persistent discussion workspace initialized.\n\n"
        "## Pending Questions\n\n"
        + "\n".join(question_lines)
        + "\n\n## Decision Evolution\n\n"
        + "\n".join(evolution_lines)
        + "\n"
    ).encode("utf-8")


def _mutation_fingerprint(request: dict[str, Any]) -> str:
    return _sha256(
        _canonical_json(
            {key: value for key, value in request.items() if key != "idempotency_key"}
        ).encode("utf-8")
    )


def _idempotent_result(
    records: dict[str, list[dict[str, Any]]], request: dict[str, Any]
) -> dict[str, Any] | None:
    key = request["idempotency_key"]
    matches = [event for event in records["Recent Events"] if event.get("idempotency_key") == key]
    if not matches:
        return None
    event = matches[-1]
    if event.get("request_fingerprint") != _mutation_fingerprint(request):
        raise ProtocolError("idempotency_conflict", "idempotency key was reused for another update")
    result = _json_field(event, "result_json", "event")
    result["idempotent_replay"] = True
    return result


def _append_event(
    records: dict[str, list[dict[str, Any]]],
    request: dict[str, Any],
    *,
    revision: int,
    event_type: str,
    result: dict[str, Any],
) -> None:
    records["Recent Events"].append(
        {
            "event_id": f"event-{revision:08d}",
            "event_type": event_type,
            "ledger_revision": revision,
            "topic_id": request["actor_topic_id"],
            "idempotency_key": request["idempotency_key"],
            "request_fingerprint": _mutation_fingerprint(request),
            "result_json": _canonical_json(result),
        }
    )
    records["Recent Events"] = records["Recent Events"][-200:]


def _write_ledger_transaction(
    ledger_path: Path,
    frontmatter: dict[str, str],
    records: dict[str, list[dict[str, Any]]],
    request: dict[str, Any],
    *,
    ledger_revision: int,
    event_type: str,
    result: dict[str, Any],
) -> None:
    _append_event(
        records,
        request,
        revision=ledger_revision,
        event_type=event_type,
        result=result,
    )
    frontmatter["ledger_revision"] = str(ledger_revision)
    frontmatter["event_count"] = str(int(frontmatter["event_count"]) + 1)
    _atomic_replace(ledger_path, _render_records_ledger(frontmatter, records))


def _validate_revisions(
    request: dict[str, Any], frontmatter: dict[str, str], topic_record: dict[str, Any]
) -> tuple[int, int]:
    ledger_revision = int(frontmatter["ledger_revision"])
    topic_revision = int(topic_record["record_revision"])
    if request["expected_ledger_revision"] != ledger_revision:
        raise ProtocolError(
            "ledger_revision_conflict",
            "expected ledger revision is stale",
            context={"ledger_revision": ledger_revision, "record_revision": topic_revision},
        )
    if request["expected_topic_revision"] != topic_revision:
        raise ProtocolError(
            "record_revision_conflict",
            "expected topic record revision is stale",
            context={"ledger_revision": ledger_revision, "record_revision": topic_revision},
        )
    return ledger_revision, topic_revision


def _validate_mutation(value: Any, key: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ProtocolError("invalid_request", "mutation must be an object")
    mutation_type = value.get("type")
    if mutation_type not in SUBSTANTIVE_MUTATIONS:
        raise ProtocolError("invalid_request", "mutation type is unsupported")
    return value


def _apply_mutation_to_records(
    records: dict[str, list[dict[str, Any]]],
    *,
    topic_id: str,
    mutation: dict[str, Any],
    idempotency_key: str,
) -> dict[str, Any]:
    seed = uuid.UUID(idempotency_key).hex
    mutation_type = mutation["type"]
    result: dict[str, Any] = {}
    if mutation_type == "confirm-decision":
        _expect_keys(mutation, {"type", "summary", "rationale"}, "confirm-decision mutation")
        decision_id = f"D-{seed}"
        data = {
            "decision_id": decision_id,
            "summary": _expect_string(mutation["summary"], "mutation.summary", max_bytes=2048),
            "rationale": _expect_string(mutation["rationale"], "mutation.rationale", max_bytes=4096),
            "state": "confirmed",
            "evolution": "confirmed",
        }
        records["Pending Items"].append(
            {"item_id": decision_id, "item_kind": "decision", "topic_id": topic_id, "data_json": _canonical_json(data)}
        )
        result["decision_id"] = decision_id
    elif mutation_type == "set-active-question":
        _expect_keys(mutation, {"type", "prompt", "recommendation", "reason"}, "set-active-question mutation")
        snapshot = _topic_snapshot(records, topic_id)
        if any(item["state"] == "active" for item in snapshot["questions"]):
            raise ProtocolError("active_question_conflict", "the topic already has one active question")
        question_id = f"Q-{seed}"
        data = {
            "question_id": question_id,
            "prompt": _expect_string(mutation["prompt"], "mutation.prompt", max_bytes=4096),
            "recommendation": _expect_string(mutation["recommendation"], "mutation.recommendation", max_bytes=4096),
            "reason": _expect_string(mutation["reason"], "mutation.reason", max_bytes=4096),
            "state": "active",
        }
        records["Pending Items"].append(
            {"item_id": question_id, "item_kind": "question", "topic_id": topic_id, "data_json": _canonical_json(data)}
        )
        result["question_id"] = question_id
    elif mutation_type == "insert-idea":
        _expect_keys(mutation, {"type", "summary"}, "insert-idea mutation")
        active_records = []
        for record in records["Pending Items"]:
            if record.get("topic_id") == topic_id and record.get("item_kind") == "question":
                data = _json_field(record, "data_json", "question")
                if data["state"] == "active":
                    data["state"] = "suspended"
                    record["data_json"] = _canonical_json(data)
                    active_records.append(data)
        if len(active_records) > 1:
            raise ProtocolError("state_corrupt", "topic has multiple active questions")
        idea_id = f"I-{seed}"
        idea = {
            "idea_id": idea_id,
            "summary": _expect_string(mutation["summary"], "mutation.summary", max_bytes=4096),
            "suspended_question_id": active_records[0]["question_id"] if active_records else None,
        }
        records["Pending Items"].append(
            {"item_id": idea_id, "item_kind": "idea", "topic_id": topic_id, "data_json": _canonical_json(idea)}
        )
        result.update({"idea_id": idea_id, "suspended_question_id": idea["suspended_question_id"]})
    elif mutation_type == "resolve-inserted-idea":
        expected = {"type", "question_id", "action"}
        if mutation.get("action") == "adjust":
            expected.add("adjusted_prompt")
        _expect_keys(mutation, expected, "resolve-inserted-idea mutation")
        action = mutation["action"]
        if action not in QUESTION_ACTIONS:
            raise ProtocolError("invalid_request", "question action is unsupported")
        record = _record_by_id(records["Pending Items"], "item_id", mutation["question_id"], "question_id")
        data = _json_field(record, "data_json", "question")
        if data["state"] != "suspended":
            raise ProtocolError("question_state_conflict", "only a suspended question can be resolved")
        data["state"] = "invalidated" if action == "invalidate" else "active"
        if action == "adjust":
            data["prompt"] = _expect_string(mutation["adjusted_prompt"], "mutation.adjusted_prompt", max_bytes=4096)
        record["data_json"] = _canonical_json(data)
        result.update({"question_id": data["question_id"], "question_action": action})
    elif mutation_type == "change-direction":
        _expect_keys(mutation, {"type", "summary", "affected_decision_ids"}, "change-direction mutation")
        affected = mutation["affected_decision_ids"]
        if not isinstance(affected, list) or not affected or len(set(affected)) != len(affected):
            raise ProtocolError("invalid_request", "affected_decision_ids must be a non-empty unique array")
        known = {item["decision_id"] for item in _topic_snapshot(records, topic_id)["decisions"]}
        if any(not isinstance(item, str) or item not in known for item in affected):
            raise ProtocolError("decision_not_found", "an affected decision is unknown")
        direction = _expect_string(mutation["summary"], "mutation.summary", max_bytes=4096)
        impact_ids = []
        for index, decision_id in enumerate(affected):
            impact_id = f"IMP-{seed}-{index + 1}"
            impact = {
                "impact_id": impact_id,
                "decision_id": decision_id,
                "direction": direction,
                "state": "pending",
                "action": None,
            }
            records["Impacts"].append(
                {"impact_id": impact_id, "topic_id": topic_id, "data_json": _canonical_json(impact)}
            )
            impact_ids.append(impact_id)
        result["impact_ids"] = impact_ids
    else:
        expected = {"type", "impact_id", "decision_id", "action", "summary"}
        _expect_keys(mutation, expected, "resolve-impact mutation")
        action = mutation["action"]
        if action not in IMPACT_ACTIONS:
            raise ProtocolError("invalid_request", "impact action is unsupported")
        record = _record_by_id(records["Impacts"], "impact_id", mutation["impact_id"], "impact_id")
        impact = _json_field(record, "data_json", "impact")
        if impact["decision_id"] != mutation["decision_id"] or impact["state"] != "pending":
            raise ProtocolError("impact_state_conflict", "impact is not pending for this decision")
        impact["state"] = "resolved"
        impact["action"] = action
        record["data_json"] = _canonical_json(impact)
        decision_record = _record_by_id(records["Pending Items"], "item_id", mutation["decision_id"], "decision_id")
        decision = _json_field(decision_record, "data_json", "decision")
        summary = _expect_string(mutation["summary"], "mutation.summary", max_bytes=4096)
        if action == "adjust":
            decision["summary"] = summary
            decision["evolution"] = f"adjusted: {summary}"
        elif action == "replace":
            decision["summary"] = summary
            decision["evolution"] = f"replaced: {summary}"
        elif action == "discard":
            decision["state"] = "discarded"
            decision["evolution"] = f"discarded: {summary}"
        else:
            decision["evolution"] = f"kept: {summary}"
        decision_record["data_json"] = _canonical_json(decision)
        result.update({"impact_id": impact["impact_id"], "decision_id": decision["decision_id"], "impact_action": action})
    return result


def _prepare_topic_update(request: dict[str, Any]) -> dict[str, Any]:
    project, ledger_path, topic_path, lock_path, owner_ref = _evolution_paths(request)
    allowed = {
        "protocol_version", "operation", "project_path", "project_id", "tree_id",
        "actor_topic_id", "actor_conversation_ref", "expected_ledger_revision",
        "expected_topic_revision", "idempotency_key", "mutation",
    }
    _expect_keys(request, allowed, "prepare-topic-update request")
    _validate_uuid4(request["idempotency_key"], "idempotency_key")
    mutation = _validate_mutation(request["mutation"], request["idempotency_key"])
    with lock_path.open("a+b") as lock_stream:
        _flock_with_timeout(lock_stream)
        frontmatter, records = _load_records(ledger_path)
        replay = _idempotent_result(records, request)
        if replay is not None:
            return replay
        topic_record = _record_by_id(records["Current Topics"], "topic_id", request["actor_topic_id"], "topic_id")
        ledger_revision, topic_revision = _validate_revisions(request, frontmatter, topic_record)
        _verify_topic_owner(
            records,
            request["actor_topic_id"],
            owner_ref,
            allow_active_grilling=True,
        )
        active_write = _active_pending_write(records)
        if active_write is not None:
            raise ProtocolError(
                "document_write_reconciliation_required",
                "a confirmed document update remains pending reconciliation",
                context={"state": "confirmed-but-pending", "ledger_revision": ledger_revision, "record_revision": topic_revision},
            )
        snapshot = _topic_snapshot(records, request["actor_topic_id"])
        suspended_questions = [
            question for question in snapshot["questions"] if question["state"] == "suspended"
        ]
        if suspended_questions and mutation["type"] != "resolve-inserted-idea":
            raise ProtocolError(
                "question_state_conflict",
                "the suspended question must be resumed, adjusted or invalidated first",
            )
        pending_impacts = [
            impact for impact in snapshot["impacts"] if impact["state"] == "pending"
        ]
        if pending_impacts and mutation["type"] != "resolve-impact":
            raise ProtocolError(
                "impact_state_conflict",
                "each pending decision impact must be resolved separately first",
            )
        current_bytes = _require_regular_nosymlink(topic_path, "topic document")
        next_records = {name: [dict(record) for record in values] for name, values in records.items()}
        mutation_result = _apply_mutation_to_records(
            next_records,
            topic_id=request["actor_topic_id"],
            mutation=mutation,
            idempotency_key=request["idempotency_key"],
        )
        next_revision = ledger_revision + 1
        next_topic_revision = topic_revision + 1
        next_snapshot = _topic_snapshot(next_records, request["actor_topic_id"])
        manifest = _parse_frontmatter(
            _require_regular_nosymlink(project / "docs" / "discussions" / ".codex-project.md", "project identity manifest"),
            "project identity manifest",
        )
        after_bytes = _render_evolved_topic(
            project_id=request["project_id"], tree_id=request["tree_id"],
            topic_id=request["actor_topic_id"], root_slug=manifest["root_slug"],
            topic_revision=next_topic_revision, snapshot=next_snapshot,
        )
        write_id = f"DW-{uuid.UUID(request['idempotency_key']).hex}"
        payload_path = ledger_path.parent / "pending-writes" / f"{write_id}.payload"
        _mkdirs(payload_path.parent, [])
        owned_payload_paths = {
            Path(item["payload_path"]) for item in records["Pending Document Writes"]
        }
        observed_payload_paths = {
            item for item in payload_path.parent.iterdir() if item.is_file()
        }
        orphan_payload_paths = observed_payload_paths - owned_payload_paths
        recovered_orphan = False
        if orphan_payload_paths:
            if orphan_payload_paths != {payload_path}:
                raise ProtocolError(
                    "orphaned_document_write",
                    "an unrelated orphan payload must be recovered by its exact request",
                )
            orphan_bytes = _require_regular_nosymlink(
                payload_path, "orphan pending document payload"
            )
            if orphan_bytes != after_bytes or _sha256(orphan_bytes) != _sha256(after_bytes):
                raise ProtocolError(
                    "document_write_orphan_conflict",
                    "the deterministic orphan payload does not match this typed request",
                )
            recovered_orphan = True
        else:
            _write_new_file(payload_path, after_bytes, [])
        payload_path.chmod(0o400)
        write_record = {
            "document_write_id": write_id,
            "topic_id": request["actor_topic_id"],
            "owner_ref": owner_ref,
            "topic_path": str(topic_path),
            "payload_path": str(payload_path),
            "before_sha256": _sha256(current_bytes),
            "after_sha256": _sha256(after_bytes),
            "state": "confirmed-but-pending",
            "lease_id": None,
            "lease_version": None,
        }
        next_records["Pending Document Writes"].append(write_record)
        next_topic_record = _record_by_id(next_records["Current Topics"], "topic_id", request["actor_topic_id"], "topic_id")
        next_topic_record["record_revision"] = next_topic_revision
        result = {
            "ok": True, "state": "confirmed-but-pending", "idempotent_replay": False,
            "project_id": request["project_id"], "tree_id": request["tree_id"], "topic_id": request["actor_topic_id"],
            "ledger_revision": next_revision, "record_revision": next_topic_revision,
            "document_write_id": write_id, "payload_path": str(payload_path),
            "before_sha256": write_record["before_sha256"], "after_sha256": write_record["after_sha256"],
            "recovered_orphan": recovered_orphan,
            **mutation_result,
        }
        _append_event(next_records, request, revision=next_revision, event_type="topic-update-prepared", result=result)
        frontmatter["ledger_revision"] = str(next_revision)
        frontmatter["event_count"] = str(int(frontmatter["event_count"]) + 1)
        if os.environ.get("CODEX_DISCUSSION_TEST_FAILPOINT") == "topic-update-ledger-replace":
            raise OSError("injected topic update ledger replace failure")
        _atomic_replace(ledger_path, _render_records_ledger(frontmatter, next_records))
        return result


def _run_supervision_cli(arguments: list[str], error_code: str) -> dict[str, Any]:
    script_path = (
        Path(__file__).parents[2]
        / "guided-implementation"
        / "scripts"
        / "supervision_protocol.py"
    )
    completed = subprocess.run(
        [sys.executable, str(script_path), *arguments],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    if completed.returncode != 0:
        raise ProtocolError(
            error_code,
            "supervision protocol rejected the document lease proof",
            cause=completed.stderr.strip() or None,
        )
    try:
        result = json.loads(completed.stdout)
    except json.JSONDecodeError as error:
        raise ProtocolError(
            error_code,
            "supervision protocol returned an invalid lease response",
            cause=str(error),
        ) from error
    if not isinstance(result, dict):
        raise ProtocolError(error_code, "supervision lease response must be an object")
    return result


def _expected_document_lease_path(project: Path) -> Path:
    return project / (".git" if (project / ".git").is_dir() else ".codex") / "cc-switch-document-lease.json"


def _verify_live_lease(
    credential: Any, *, project: Path, owner_ref: str
) -> tuple[dict[str, Any], Path]:
    if not isinstance(credential, dict):
        raise ProtocolError("document_lease_invalid", "document_lease must be an object")
    _expect_keys(credential, {"path", "lease_id", "version"}, "document_lease")
    path = Path(_expect_string(credential["path"], "document_lease.path", max_bytes=4096))
    if path != _expected_document_lease_path(project):
        raise ProtocolError("document_lease_invalid", "document lease path does not belong to this checkout")
    document = _run_supervision_cli(
        [
            "verify-document-lease", "--file", str(path),
            "--id", str(credential["lease_id"]),
            "--version", str(credential["version"]),
        ],
        "document_lease_invalid",
    )
    holder = document.get("holder")
    if (
        document.get("repository") != str(project)
        or document.get("version") != credential["version"]
        or not isinstance(holder, dict)
        or holder.get("lease_id") != credential["lease_id"]
        or holder.get("owner_task_id") != owner_ref
        or holder.get("purpose") != "document-write"
        or holder.get("stage") != "design-discussion"
        or not isinstance(holder.get("expires_at_epoch"), int)
        or document.get("verified") is not True
        or holder["expires_at_epoch"] <= int(time.time())
    ):
        raise ProtocolError("document_lease_invalid", "document lease proof is stale or does not authorize this write")
    return document, path


def _apply_document_write(request: dict[str, Any]) -> dict[str, Any]:
    project, ledger_path, topic_path, lock_path, owner_ref = _evolution_paths(request)
    _expect_keys(
        request,
        {"protocol_version", "operation", "project_path", "project_id", "tree_id", "actor_topic_id", "actor_conversation_ref", "expected_ledger_revision", "expected_topic_revision", "idempotency_key", "document_write_id", "document_lease"},
        "apply-document-write request",
    )
    _validate_uuid4(request["idempotency_key"], "idempotency_key")
    lease, _ = _verify_live_lease(
        request["document_lease"], project=project, owner_ref=owner_ref
    )
    with lock_path.open("a+b") as lock_stream:
        _flock_with_timeout(lock_stream)
        frontmatter, records = _load_records(ledger_path)
        replay = _idempotent_result(records, request)
        if replay is not None:
            return replay
        topic_record = _record_by_id(records["Current Topics"], "topic_id", request["actor_topic_id"], "topic_id")
        ledger_revision, topic_revision = _validate_revisions(request, frontmatter, topic_record)
        _verify_topic_owner(
            records,
            request["actor_topic_id"],
            owner_ref,
            allow_active_grilling=True,
        )
        write = _record_by_id(records["Pending Document Writes"], "document_write_id", request["document_write_id"], "document_write_id")
        if write["owner_ref"] != owner_ref or write["state"] != "confirmed-but-pending":
            raise ProtocolError("document_write_state_conflict", "document write is not pending for this owner")
        payload_path = Path(write["payload_path"])
        payload = _require_regular_nosymlink(payload_path, "pending document payload")
        if _sha256(payload) != write["after_sha256"]:
            raise ProtocolError("document_write_payload_damaged", "pending document payload digest does not match")
        current = _require_regular_nosymlink(topic_path, "topic document")
        current_digest = _sha256(current)
        if current_digest == write["before_sha256"]:
            _atomic_replace(topic_path, payload)
        elif current_digest != write["after_sha256"]:
            raise ProtocolError("document_write_before_conflict", "topic document changed since the write was prepared")
        verified = _require_regular_nosymlink(topic_path, "topic document")
        if _sha256(verified) != write["after_sha256"] or verified != payload:
            raise ProtocolError("document_write_verification_failed", "topic document did not verify after apply")
        next_revision = ledger_revision + 1
        next_topic_revision = topic_revision
        write["state"] = "applied-pending-release"
        write["lease_id"] = request["document_lease"]["lease_id"]
        write["lease_version"] = request["document_lease"]["version"]
        result = {
            "ok": True, "state": "applied-pending-release", "idempotent_replay": False,
            "project_id": request["project_id"], "tree_id": request["tree_id"], "topic_id": request["actor_topic_id"],
            "ledger_revision": next_revision, "record_revision": next_topic_revision,
            "document_write_id": write["document_write_id"], "document_verified": True,
            "after_sha256": write["after_sha256"], "lease_id": lease["holder"]["lease_id"],
            "lease_version": lease["version"], "release_allowed": True,
        }
        _append_event(records, request, revision=next_revision, event_type="document-write-applied", result=result)
        frontmatter["ledger_revision"] = str(next_revision)
        frontmatter["event_count"] = str(int(frontmatter["event_count"]) + 1)
        _atomic_replace(ledger_path, _render_records_ledger(frontmatter, records))
        return result


def _complete_document_write(request: dict[str, Any]) -> dict[str, Any]:
    project, ledger_path, topic_path, lock_path, owner_ref = _evolution_paths(request)
    _expect_keys(
        request,
        {"protocol_version", "operation", "project_path", "project_id", "tree_id", "actor_topic_id", "actor_conversation_ref", "expected_ledger_revision", "expected_topic_revision", "idempotency_key", "document_write_id", "document_lease_release"},
        "complete-document-write request",
    )
    _validate_uuid4(request["idempotency_key"], "idempotency_key")
    release = request["document_lease_release"]
    if not isinstance(release, dict):
        raise ProtocolError("document_lease_invalid", "document_lease_release must be an object")
    _expect_keys(release, {"path", "lease_id", "version"}, "document_lease_release")
    lease_path = Path(
        _expect_string(release["path"], "document_lease_release.path", max_bytes=4096)
    )
    if lease_path != _expected_document_lease_path(project):
        raise ProtocolError(
            "document_lease_release_unverified",
            "document lease path does not belong to this checkout",
        )
    lease_document = _run_supervision_cli(
        ["inspect-document-lease", "--repository", str(project)],
        "document_lease_release_unverified",
    )
    with lock_path.open("a+b") as lock_stream:
        _flock_with_timeout(lock_stream)
        frontmatter, records = _load_records(ledger_path)
        replay = _idempotent_result(records, request)
        if replay is not None:
            return replay
        topic_record = _record_by_id(records["Current Topics"], "topic_id", request["actor_topic_id"], "topic_id")
        ledger_revision, topic_revision = _validate_revisions(request, frontmatter, topic_record)
        _verify_topic_owner(
            records,
            request["actor_topic_id"],
            owner_ref,
            allow_active_grilling=True,
        )
        write = _record_by_id(records["Pending Document Writes"], "document_write_id", request["document_write_id"], "document_write_id")
        if write["state"] != "applied-pending-release" or write["lease_id"] != release["lease_id"]:
            raise ProtocolError("document_write_state_conflict", "document write is not awaiting this lease release")
        if lease_document.get("path") != str(lease_path) or lease_document.get("state") != "available" or lease_document.get("holder") is not None or lease_document.get("version") != release["version"] or release["version"] != write["lease_version"] + 1:
            raise ProtocolError("document_lease_release_unverified", "document lease release cannot be verified")
        current = _require_regular_nosymlink(topic_path, "topic document")
        if _sha256(current) != write["after_sha256"]:
            raise ProtocolError("document_write_verification_failed", "topic document changed before release completion")
        next_revision = ledger_revision + 1
        next_topic_revision = topic_revision
        write["state"] = "completed"
        result = {
            "ok": True, "state": "completed", "idempotent_replay": False,
            "project_id": request["project_id"], "tree_id": request["tree_id"], "topic_id": request["actor_topic_id"],
            "ledger_revision": next_revision, "record_revision": next_topic_revision,
            "document_write_id": write["document_write_id"], "document_verified": True,
            "release_verified": True, "after_sha256": write["after_sha256"],
        }
        _append_event(records, request, revision=next_revision, event_type="document-write-completed", result=result)
        frontmatter["ledger_revision"] = str(next_revision)
        frontmatter["event_count"] = str(int(frontmatter["event_count"]) + 1)
        _atomic_replace(ledger_path, _render_records_ledger(frontmatter, records))
        return result


def _reconcile_document_write(request: dict[str, Any]) -> dict[str, Any]:
    project, ledger_path, topic_path, lock_path, owner_ref = _evolution_paths(request)
    _expect_keys(
        request,
        {
            "protocol_version", "operation", "project_path", "project_id", "tree_id",
            "actor_topic_id", "actor_conversation_ref", "expected_ledger_revision",
            "expected_topic_revision", "idempotency_key", "document_write_id",
        },
        "reconcile-document-write request",
    )
    _validate_uuid4(request["idempotency_key"], "idempotency_key")
    lease_document = _run_supervision_cli(
        ["inspect-document-lease", "--repository", str(project)],
        "reconciliation_conflict",
    )
    with lock_path.open("a+b") as lock_stream:
        _flock_with_timeout(lock_stream)
        frontmatter, records = _load_records(ledger_path)
        replay = _idempotent_result(records, request)
        if replay is not None:
            return replay
        topic_record = _record_by_id(
            records["Current Topics"], "topic_id", request["actor_topic_id"], "topic_id"
        )
        ledger_revision, topic_revision = _validate_revisions(
            request, frontmatter, topic_record
        )
        _verify_topic_owner(
            records,
            request["actor_topic_id"],
            owner_ref,
            allow_active_grilling=True,
        )
        write = _record_by_id(
            records["Pending Document Writes"],
            "document_write_id",
            request["document_write_id"],
            "document_write_id",
        )
        if write["owner_ref"] != owner_ref or write["state"] == "completed":
            raise ProtocolError(
                "document_write_state_conflict",
                "document write is not reconcilable for this owner",
            )
        payload = _require_regular_nosymlink(Path(write["payload_path"]), "pending document payload")
        if _sha256(payload) != write["after_sha256"]:
            raise ProtocolError(
                "document_write_payload_damaged",
                "pending document payload digest does not match",
            )
        current_digest = _sha256(
            _require_regular_nosymlink(topic_path, "topic document")
        )
        next_state: str
        if write["state"] == "confirmed-but-pending":
            if current_digest == write["before_sha256"]:
                next_state = "confirmed-but-pending"
            elif current_digest == write["after_sha256"]:
                holder = lease_document.get("holder")
                if (
                    lease_document.get("state") != "held"
                    or not isinstance(holder, dict)
                    or holder.get("owner_task_id") != owner_ref
                    or holder.get("stage") != "design-discussion"
                    or holder.get("purpose") != "document-write"
                ):
                    raise ProtocolError(
                        "reconciliation_conflict",
                        "applied bytes exist but the authorizing lease cannot be proven",
                    )
                write["state"] = "applied-pending-release"
                write["lease_id"] = holder["lease_id"]
                write["lease_version"] = lease_document["version"]
                next_state = "applied-pending-release"
            else:
                raise ProtocolError(
                    "document_write_before_conflict",
                    "topic document matches neither pending write digest",
                )
        else:
            if current_digest != write["after_sha256"]:
                raise ProtocolError(
                    "document_write_verification_failed",
                    "applied document bytes no longer match the pending payload",
                )
            if (
                lease_document.get("state") == "available"
                and lease_document.get("holder") is None
                and lease_document.get("version") == write["lease_version"] + 1
            ):
                write["state"] = "completed"
                next_state = "completed"
            elif (
                lease_document.get("state") == "held"
                and isinstance(lease_document.get("holder"), dict)
                and lease_document["holder"].get("lease_id") == write["lease_id"]
                and lease_document.get("version") == write["lease_version"]
            ):
                next_state = "applied-pending-release"
            else:
                raise ProtocolError(
                    "reconciliation_conflict",
                    "document lease state cannot be reconciled with the pending write",
                )
        next_revision = ledger_revision + 1
        result = {
            "ok": True,
            "state": next_state,
            "idempotent_replay": False,
            "project_id": request["project_id"],
            "tree_id": request["tree_id"],
            "topic_id": request["actor_topic_id"],
            "ledger_revision": next_revision,
            "record_revision": topic_revision,
            "document_write_id": write["document_write_id"],
            "document_verified": current_digest == write["after_sha256"],
            "release_verified": next_state == "completed",
        }
        _append_event(
            records,
            request,
            revision=next_revision,
            event_type="document-write-reconciled",
            result=result,
        )
        frontmatter["ledger_revision"] = str(next_revision)
        frontmatter["event_count"] = str(int(frontmatter["event_count"]) + 1)
        _atomic_replace(ledger_path, _render_records_ledger(frontmatter, records))
        return result


def _validate_pending_writes(
    ledger_path: Path, records: dict[str, list[dict[str, Any]]]
) -> None:
    expected_paths: set[Path] = set()
    for write in records["Pending Document Writes"]:
        path = Path(write["payload_path"])
        expected_paths.add(path)
        payload = _require_regular_nosymlink(path, "pending document payload")
        if _sha256(payload) != write["after_sha256"]:
            raise ProtocolError("document_write_payload_damaged", "pending document payload digest does not match")
    directory = ledger_path.parent / "pending-writes"
    if directory.exists():
        observed = {path for path in directory.iterdir() if path.is_file()}
        if observed != expected_paths:
            raise ProtocolError("orphaned_document_write", "pending-writes contains an unowned or missing payload")


CHECKPOINT_PURPOSES = {"pause", "handoff", "split", "stage-entry", "implementation-source"}
CHECKPOINT_ACTIVE_STATES = {"prepared", "outcome-unknown"}
CHECKPOINT_GC_ACTIVE_STATES = {"outcome-unknown"}


def _inject_failure(name: str) -> None:
    if os.environ.get("CODEX_DISCUSSION_TEST_FAILPOINT") == name:
        raise ProtocolError(
            "injected_failure", f"injected failure at checkpoint boundary: {name}"
        )
def _checkpoint_id(idempotency_key: str) -> str:
    return f"CP-{uuid.UUID(idempotency_key).hex}"


def _checkpoint_gc_id(idempotency_key: str) -> str:
    return f"GC-{uuid.UUID(idempotency_key).hex}"


def _checkpoint_record(
    records: dict[str, list[dict[str, Any]]], checkpoint_id: str
) -> dict[str, Any]:
    return _record_by_id(records["Checkpoints"], "checkpoint_id", checkpoint_id, "checkpoint_id")


def _checkpoint_data(record: dict[str, Any]) -> dict[str, Any]:
    return _json_field(record, "data_json", "checkpoint")


def _store_checkpoint(record: dict[str, Any], checkpoint: dict[str, Any]) -> None:
    record["state"] = checkpoint["state"]
    record["record_revision"] = checkpoint["record_revision"]
    record["data_json"] = _canonical_json(checkpoint)


def _active_checkpoint(records: dict[str, list[dict[str, Any]]]) -> dict[str, Any] | None:
    active = [
        record
        for record in records["Checkpoints"]
        if record.get("state") in CHECKPOINT_ACTIVE_STATES
    ]
    if len(active) > 1:
        raise ProtocolError("state_corrupt", "more than one checkpoint intent is active")
    return active[0] if active else None


def _checkpoint_gc_data(record: dict[str, Any]) -> dict[str, Any]:
    return _json_field(record, "data_json", "checkpoint GC")


def _checkpoint_gc_record(
    records: dict[str, list[dict[str, Any]]], gc_operation_id: str
) -> dict[str, Any]:
    return _record_by_id(
        records["Phase Results"],
        "result_id",
        gc_operation_id,
        "gc_operation_id",
    )


def _store_checkpoint_gc(record: dict[str, Any], operation: dict[str, Any]) -> None:
    record["state"] = operation["state"]
    record["record_revision"] = operation["record_revision"]
    record["data_json"] = _canonical_json(operation)


def _active_checkpoint_gc(
    records: dict[str, list[dict[str, Any]]],
) -> dict[str, Any] | None:
    active = [
        record
        for record in records["Phase Results"]
        if record.get("result_kind") == "checkpoint-gc"
        and record.get("state") in CHECKPOINT_GC_ACTIVE_STATES
    ]
    if len(active) > 1:
        raise ProtocolError("state_corrupt", "more than one checkpoint GC is active")
    return active[0] if active else None


def _checkpoint_decision_digest(records: dict[str, list[dict[str, Any]]], topic_id: str) -> str:
    decisions = _topic_snapshot(records, topic_id)["decisions"]
    normalized = [
        {
            "decision_id": item["decision_id"],
            "evolution": item.get("evolution"),
            "rationale": item.get("rationale"),
            "state": item.get("state"),
            "summary": item["summary"],
        }
        for item in decisions
    ]
    return _sha256(_canonical_json(normalized).encode("utf-8"))


def _checkpoint_decision_authority(
    records: dict[str, list[dict[str, Any]]], topic_id: str
) -> tuple[dict[str, str], list[str]]:
    decisions = [
        _json_field(record, "data_json", "decision")
        for record in records["Pending Items"]
        if record.get("topic_id") == topic_id and record.get("item_kind") == "decision"
    ]
    digests = {
        item["decision_id"]: _sha256(_canonical_json(item).encode("utf-8"))
        for item in sorted(decisions, key=lambda item: item["decision_id"])
    }
    confirmed = sorted(
        item["decision_id"] for item in decisions if item.get("state") == "confirmed"
    )
    return digests, confirmed


def _checkpoint_authority_context(
    request: dict[str, Any], ledger_revision: int, checkpoint: dict[str, Any]
) -> dict[str, Any]:
    return {
        "state": checkpoint["state"],
        "ledger_revision": ledger_revision,
        "record_revision": checkpoint["record_revision"],
        "project_id": request["project_id"],
        "tree_id": request["tree_id"],
        "topic_id": request["actor_topic_id"],
        "checkpoint_id": checkpoint["checkpoint_id"],
    }


def _project_relative_path(project: Path, path: Path, label: str) -> str:
    try:
        relative = path.relative_to(project)
    except ValueError as error:
        raise ProtocolError("invalid_request", f"{label} must be inside the project") from error
    text = relative.as_posix()
    if not text or text.startswith("../") or text == ".git" or text.startswith(".git/"):
        raise ProtocolError("invalid_request", f"{label} is not a safe project-relative path")
    return text


def _git(project: Path, arguments: list[str], *, input_bytes: bytes | None = None) -> bytes:
    completed = subprocess.run(
        ["git", *arguments],
        cwd=project,
        input=input_bytes,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    if completed.returncode != 0:
        raise ProtocolError(
            "checkpoint_history_mismatch",
            f"Git command failed: git {' '.join(arguments)}",
            cause=completed.stderr.decode("utf-8", errors="replace").strip(),
        )
    return completed.stdout


def _git_object_type(project: Path, object_id: str) -> str | None:
    completed = subprocess.run(
        ["git", "cat-file", "-t", object_id],
        cwd=project,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    return completed.stdout.strip() if completed.returncode == 0 else None


def _verify_git_base(project: Path, base_ref: str) -> str:
    completed = subprocess.run(
        ["git", "rev-parse", "--verify", f"{base_ref}^{{commit}}"],
        cwd=project,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    if completed.returncode != 0 or not re.fullmatch(r"[0-9a-f]{40,64}", completed.stdout.strip()):
        raise ProtocolError("checkpoint_base_invalid", "checkpoint base_ref must resolve to a commit")
    return completed.stdout.strip()


def _tree_entries(project: Path, tree_id: str) -> dict[str, tuple[str, str]]:
    output = _git(project, ["ls-tree", "-rz", tree_id])
    entries: dict[str, tuple[str, str]] = {}
    for raw in output.split(b"\0"):
        if not raw:
            continue
        metadata, path_bytes = raw.split(b"\t", 1)
        mode, object_type, object_id = metadata.decode("ascii").split(" ")
        if object_type not in {"blob", "commit", "tree"}:
            raise ProtocolError("checkpoint_history_mismatch", "Git tree contains an unsupported object")
        entries[path_bytes.decode("utf-8")] = (mode, object_id)
    return entries


def _create_checkpoint_tree(
    project: Path, parent_commit: str, path_blobs: dict[str, str]
) -> str:
    parent_tree = _git(project, ["show", "-s", "--format=%T", parent_commit]).decode("ascii").strip()
    index_file = Path(tempfile.mkstemp(prefix="discussion-checkpoint-index-")[1])
    try:
        environment = dict(os.environ)
        environment["GIT_INDEX_FILE"] = str(index_file)
        index_file.unlink()
        read = subprocess.run(
            ["git", "read-tree", parent_tree],
            cwd=project,
            env=environment,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        if read.returncode != 0:
            raise ProtocolError("checkpoint_history_mismatch", "cannot seed checkpoint tree", cause=read.stderr.strip())
        index_input = b"".join(
            f"100644 {blob}\t{path}\0".encode("utf-8")
            for path, blob in sorted(path_blobs.items())
        )
        update = subprocess.run(
            ["git", "update-index", "-z", "--index-info"],
            cwd=project,
            env=environment,
            input=index_input,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        if update.returncode != 0:
            raise ProtocolError(
                "checkpoint_history_mismatch",
                "cannot write checkpoint paths to temporary index",
                cause=update.stderr.decode("utf-8", errors="replace").strip(),
            )
        write_tree = subprocess.run(
            ["git", "write-tree"],
            cwd=project,
            env=environment,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        if write_tree.returncode != 0:
            raise ProtocolError("checkpoint_history_mismatch", "cannot write checkpoint tree", cause=write_tree.stderr.strip())
        return write_tree.stdout.strip()
    finally:
        try:
            index_file.unlink()
        except FileNotFoundError:
            pass


def _checkpoint_commit_message(checkpoint: dict[str, Any]) -> str:
    paths = json.loads(checkpoint["paths_json"])
    document_digests = json.loads(checkpoint["document_digests_json"])
    return (
        f"discussion checkpoint: {checkpoint['purpose']}\n\n"
        f"Codex-Discussion-Checkpoint: {checkpoint['checkpoint_id']}\n"
        f"Codex-Document-SHA256: {document_digests[paths[0]]}\n"
        f"Codex-Discussion-Decision-SHA256: {checkpoint['decision_digest']}\n"
        f"Codex-Discussion-Paths-SHA256: {checkpoint['path_set_digest']}\n"
    )


def _commit_metadata(project: Path, commit_id: str) -> dict[str, Any]:
    raw = _git(project, ["cat-file", "-p", commit_id]).decode("utf-8")
    header, message = raw.split("\n\n", 1)
    parents = [line.split(" ", 1)[1] for line in header.splitlines() if line.startswith("parent ")]
    trees = [line.split(" ", 1)[1] for line in header.splitlines() if line.startswith("tree ")]
    trailers: dict[str, str] = {}
    for line in message.splitlines():
        if ": " in line:
            key, value = line.split(": ", 1)
            if key.startswith("Codex-"):
                if key in trailers:
                    raise ProtocolError("checkpoint_history_mismatch", "checkpoint trailer is duplicated")
                trailers[key] = value
    return {"message": message, "parents": parents, "trailers": trailers, "tree": trees[0] if len(trees) == 1 else None}


def _commit_matches_checkpoint(
    project: Path,
    commit_id: str,
    checkpoint: dict[str, Any],
    *,
    expected_parent: str | None = None,
) -> dict[str, Any] | None:
    if _git_object_type(project, commit_id) != "commit":
        return None
    metadata = _commit_metadata(project, commit_id)
    parent_commit = expected_parent or checkpoint["base_commit"]
    if metadata["parents"] != [parent_commit]:
        return None
    paths = json.loads(checkpoint["paths_json"])
    blobs = json.loads(checkpoint["blob_ids_json"])
    digests = json.loads(checkpoint["document_digests_json"])
    expected_trailers = {
        "Codex-Discussion-Checkpoint": checkpoint["checkpoint_id"],
        "Codex-Document-SHA256": digests[paths[0]],
        "Codex-Discussion-Decision-SHA256": checkpoint["decision_digest"],
        "Codex-Discussion-Paths-SHA256": checkpoint["path_set_digest"],
    }
    if metadata["trailers"] != expected_trailers:
        return None
    parent_tree = _git(project, ["show", "-s", "--format=%T", parent_commit]).decode("ascii").strip()
    parent_entries = _tree_entries(project, parent_tree)
    expected_entries = dict(parent_entries)
    for path in paths:
        expected_entries[path] = ("100644", blobs[path])
    observed_entries = _tree_entries(project, metadata["tree"])
    if observed_entries != expected_entries:
        return None
    for path in paths:
        content = _git(project, ["cat-file", "blob", blobs[path]])
        if _sha256(content) != digests[path]:
            return None
    return {"commit_id": commit_id, "tree_id": metadata["tree"]}


def _all_checkpoint_candidates(
    project: Path,
    checkpoint: dict[str, Any],
    *,
    expected_parent: str | None = None,
) -> list[dict[str, Any]]:
    completed = subprocess.run(
        [
            "git", "cat-file", "--batch-all-objects",
            "--batch-check=%(objectname) %(objecttype)",
        ],
        cwd=project,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    if completed.returncode != 0:
        raise ProtocolError("checkpoint_history_mismatch", "cannot enumerate Git history", cause=completed.stderr.strip())
    matches = []
    commit_ids = [
        line.split(" ", 1)[0]
        for line in completed.stdout.splitlines()
        if line.endswith(" commit")
    ]
    for commit_id in dict.fromkeys(commit_ids):
        match = _commit_matches_checkpoint(
            project, commit_id, checkpoint, expected_parent=expected_parent
        )
        if match is not None:
            matches.append(match)
    return matches


def _verify_repository_coordination_lease(
    credential: Any, *, project: Path, owner_ref: str
) -> dict[str, Any]:
    if not isinstance(credential, dict):
        raise ProtocolError("repository_coordination_lease_invalid", "repository_coordination_lease must be an object")
    _expect_keys(credential, {"path", "lease_id", "version"}, "repository_coordination_lease")
    result = _run_supervision_cli(
        [
            "verify-repository-coordination-lease",
            "--file", str(credential["path"]),
            "--id", str(credential["lease_id"]),
            "--version", str(credential["version"]),
        ],
        "repository_coordination_lease_invalid",
    )
    holder = result.get("holder")
    if (
        result.get("repository") != str(project)
        or result.get("verified") is not True
        or not isinstance(holder, dict)
        or holder.get("owner_task_id") != owner_ref
        or holder.get("stage") != "design-discussion"
        or holder.get("purpose") != "checkpoint-publish"
        or result.get("version") != credential["version"]
        or result.get("path") != credential["path"]
    ):
        raise ProtocolError("repository_coordination_lease_invalid", "repository coordination lease does not authorize this checkpoint")
    return result


def _checkpoint_paths(request: dict[str, Any]) -> tuple[Path, Path, Path, Path, str, str]:
    project, ledger_path, topic_path, lock_path, owner_ref = _evolution_paths(request)
    _, storage_kind = _coordination_root(project)
    return project, ledger_path, topic_path, lock_path, owner_ref, storage_kind


def _prepare_checkpoint(request: dict[str, Any]) -> dict[str, Any]:
    project, ledger_path, topic_path, lock_path, owner_ref, storage_kind = _checkpoint_paths(request)
    _expect_keys(
        request,
        {
            "protocol_version", "operation", "project_path", "project_id", "tree_id",
            "actor_topic_id", "actor_conversation_ref", "expected_ledger_revision",
            "expected_topic_revision", "idempotency_key", "purpose", "base_ref",
        },
        "prepare-checkpoint request",
    )
    _validate_uuid4(request["idempotency_key"], "idempotency_key")
    purpose = _expect_string(request["purpose"], "purpose", max_bytes=128)
    if purpose not in CHECKPOINT_PURPOSES:
        raise ProtocolError("invalid_request", "checkpoint purpose is unsupported")
    base_ref = _expect_string(request["base_ref"], "base_ref", max_bytes=512)
    with lock_path.open("a+b") as lock_stream:
        _flock_with_timeout(lock_stream)
        frontmatter, records = _load_records(ledger_path)
        checkpoint_id = _checkpoint_id(request["idempotency_key"])
        existing_records = [
            record
            for record in records["Checkpoints"]
            if record.get("checkpoint_id") == checkpoint_id
        ]
        if len(existing_records) > 1:
            raise ProtocolError("state_corrupt", "checkpoint identity is duplicated")
        if existing_records:
            existing = _checkpoint_data(existing_records[0])
            fingerprint = _mutation_fingerprint(request)
            if (
                existing.get("creation_idempotency_key") != request["idempotency_key"]
                or existing.get("creation_fingerprint") != fingerprint
            ):
                raise ProtocolError(
                    "idempotency_conflict",
                    "checkpoint creation identity was reused with different frozen intent",
                )
            creation_result = json.loads(existing["creation_result_json"])
            creation_result["idempotent_replay"] = True
            creation_result["state"] = existing["state"]
            creation_result["checkpoint_record_revision"] = existing["record_revision"]
            creation_result["identity_reusable"] = False
            return creation_result
        replay = _idempotent_result(records, request)
        if replay is not None:
            return replay
        topic_record = _record_by_id(records["Current Topics"], "topic_id", request["actor_topic_id"], "topic_id")
        ledger_revision, topic_revision = _validate_revisions(request, frontmatter, topic_record)
        _verify_topic_owner(records, request["actor_topic_id"], owner_ref)
        if _active_pending_write(records) is not None:
            raise ProtocolError("document_write_reconciliation_required", "document write must complete before checkpoint preparation")
        active_gc = _active_checkpoint_gc(records)
        if active_gc is not None:
            raise ProtocolError(
                "checkpoint_gc_reconciliation_required",
                "checkpoint GC must be reconciled before preparing another checkpoint",
                context={"gc_operation_id": active_gc["result_id"]},
            )
        active = _active_checkpoint(records)
        if active is not None:
            active_data = _checkpoint_data(active)
            code = (
                "checkpoint_reconciliation_required"
                if active_data["state"] == "outcome-unknown"
                else "checkpoint_identity_conflict"
            )
            raise ProtocolError(code, "an older checkpoint intent must be resolved first", context={"state": active_data["state"]})
        document = _require_regular_nosymlink(topic_path, "topic document")
        relative_path = _project_relative_path(project, topic_path, "topic document")
        decision_digest = _checkpoint_decision_digest(records, request["actor_topic_id"])
        decision_digests, confirmed_decision_ids = _checkpoint_decision_authority(
            records, request["actor_topic_id"]
        )
        document_digest = _sha256(document)
        paths = [relative_path]
        digests = {relative_path: document_digest}
        blob_ids: dict[str, str] = {}
        base_commit = None
        snapshot_digest = None
        snapshot_bytes_b64 = None
        if storage_kind == "git":
            base_commit = _verify_git_base(project, base_ref)
            blob_ids[relative_path] = _git(project, ["hash-object", "-w", "--stdin"], input_bytes=document).decode("ascii").strip()
        else:
            snapshot = {
                "decision_digest": decision_digest,
                "documents": {relative_path: base64.b64encode(document).decode("ascii")},
                "document_digests": digests,
                "path_set_digest": _sha256(_canonical_json(paths).encode("utf-8")),
                "paths": paths,
                "purpose": purpose,
            }
            snapshot_bytes = (_canonical_json(snapshot) + "\n").encode("utf-8")
            snapshot_digest = _sha256(snapshot_bytes)
            snapshot_bytes_b64 = base64.b64encode(snapshot_bytes).decode("ascii")
        next_revision = ledger_revision + 1
        stage_entry_phase = topic_record.get("current_phase") if purpose == "stage-entry" else None
        stage_entry_phase_result_id = None
        if purpose == "stage-entry":
            for phase_result_record in reversed(records["Phase Results"]):
                if (
                    phase_result_record.get("result_kind") != "phase-result"
                    or phase_result_record.get("state") != "completed"
                ):
                    continue
                phase_result = _json_field(
                    phase_result_record, "data_json", "phase result"
                )
                if phase_result.get("to_phase") != stage_entry_phase:
                    continue
                phase_run_record = _record_by_id(
                    records["Phase Runs"],
                    "run_id",
                    phase_result.get("phase_run_id"),
                    "phase_run_id",
                )
                phase_run = _json_field(
                    phase_run_record, "data_json", "phase run"
                )
                if (
                    phase_run_record.get("state") == "completed"
                    and phase_run.get("source_topic_id") == request["actor_topic_id"]
                    and phase_run.get("to_phase") == stage_entry_phase
                ):
                    stage_entry_phase_result_id = phase_result["result_id"]
                    break
        checkpoint = {
            "checkpoint_id": checkpoint_id,
            "record_revision": 1,
            "project_id": request["project_id"],
            "authority_tree_id": request["tree_id"],
            "topic_id": request["actor_topic_id"],
            "purpose": purpose,
            "storage_kind": storage_kind,
            "state": "prepared",
            "base_ref": base_ref,
            "base_commit": base_commit,
            "paths_json": _canonical_json(paths),
            "path_set_digest": _sha256(_canonical_json(paths).encode("utf-8")),
            "document_digests_json": _canonical_json(digests),
            "blob_ids_json": _canonical_json(blob_ids),
            "decision_digest": decision_digest,
            "decision_digests_json": _canonical_json(decision_digests),
            "confirmed_decision_ids_json": _canonical_json(confirmed_decision_ids),
            "published_identity": None,
            "tree_id": None,
            "broken_identity": None,
            "replacement_identity": None,
            "repaired_from": None,
            "active_source_ack_revision": None,
            "snapshot_digest": snapshot_digest,
            "snapshot_bytes_b64": snapshot_bytes_b64,
            "stage_entry_phase": stage_entry_phase,
            "stage_entry_phase_result_id": stage_entry_phase_result_id,
            "creation_idempotency_key": request["idempotency_key"],
            "creation_fingerprint": _mutation_fingerprint(request),
            "creation_result_json": None,
        }
        record = {
            "checkpoint_id": checkpoint_id,
            "topic_id": request["actor_topic_id"],
            "state": "prepared",
            "record_revision": 1,
            "data_json": _canonical_json(checkpoint),
        }
        records["Checkpoints"].append(record)
        result = {
            "ok": True, "state": "prepared", "idempotent_replay": False,
            "project_id": request["project_id"], "tree_id": request["tree_id"],
            "topic_id": request["actor_topic_id"], "ledger_revision": next_revision,
            "record_revision": topic_revision, "checkpoint_record_revision": 1,
            "checkpoint_id": checkpoint_id, "storage_kind": storage_kind,
            "purpose": purpose, "base_ref": base_ref, "base_commit": base_commit,
            "paths": paths, "path_set_digest": checkpoint["path_set_digest"],
            "document_digests": digests, "decision_digest": decision_digest,
            "stage_entry_phase": stage_entry_phase,
            "stage_entry_phase_result_id": stage_entry_phase_result_id,
            "identity_reusable": False,
        }
        checkpoint["creation_result_json"] = _canonical_json(result)
        record["data_json"] = _canonical_json(checkpoint)
        _inject_failure("checkpoint-before-prepared-ledger-write")
        _write_ledger_transaction(ledger_path, frontmatter, records, request, ledger_revision=next_revision, event_type="checkpoint-prepared", result=result)
        _inject_failure("checkpoint-after-prepared-ledger-write")
        return result


def _cancel_checkpoint(request: dict[str, Any]) -> dict[str, Any]:
    project, ledger_path, topic_path, lock_path, owner_ref, _ = _checkpoint_paths(request)
    _expect_keys(
        request,
        {
            "protocol_version", "operation", "project_path", "project_id", "tree_id",
            "actor_topic_id", "actor_conversation_ref", "expected_ledger_revision",
            "expected_topic_revision", "idempotency_key", "checkpoint_id", "reason",
        },
        "cancel-checkpoint request",
    )
    _validate_uuid4(request["idempotency_key"], "idempotency_key")
    reason = _expect_string(request["reason"], "reason", max_bytes=2048)
    with lock_path.open("a+b") as lock_stream:
        _flock_with_timeout(lock_stream)
        frontmatter, records = _load_records(ledger_path)
        replay = _idempotent_result(records, request)
        if replay is not None:
            return replay
        topic_record = _record_by_id(records["Current Topics"], "topic_id", request["actor_topic_id"], "topic_id")
        ledger_revision, topic_revision = _validate_revisions(request, frontmatter, topic_record)
        _verify_topic_owner(records, request["actor_topic_id"], owner_ref)
        record = _checkpoint_record(records, _expect_string(request["checkpoint_id"], "checkpoint_id"))
        checkpoint = _checkpoint_data(record)
        if checkpoint["state"] != "prepared":
            raise ProtocolError("checkpoint_identity_conflict", "only an uncommitted prepared checkpoint can be cancelled")
        current_digest = _sha256(_require_regular_nosymlink(topic_path, "topic document"))
        frozen = json.loads(checkpoint["document_digests_json"])[json.loads(checkpoint["paths_json"])[0]]
        checkpoint["state"] = "cancelled"
        checkpoint["record_revision"] += 1
        checkpoint["cancel_reason"] = reason
        checkpoint["draft_changed"] = current_digest != frozen
        _store_checkpoint(record, checkpoint)
        next_revision = ledger_revision + 1
        result = {
            "ok": True, "state": "cancelled", "idempotent_replay": False,
            "project_id": request["project_id"], "tree_id": request["tree_id"], "topic_id": request["actor_topic_id"],
            "ledger_revision": next_revision, "record_revision": topic_revision,
            "checkpoint_id": checkpoint["checkpoint_id"], "checkpoint_record_revision": checkpoint["record_revision"],
            "draft_changed": checkpoint["draft_changed"], "identity_reusable": False,
        }
        _write_ledger_transaction(ledger_path, frontmatter, records, request, ledger_revision=next_revision, event_type="checkpoint-cancelled", result=result)
        return result


def _publish_git_checkpoint(request: dict[str, Any]) -> dict[str, Any]:
    project, ledger_path, topic_path, lock_path, owner_ref, storage_kind = _checkpoint_paths(request)
    if storage_kind != "git":
        raise ProtocolError("checkpoint_identity_conflict", "Git checkpoint publication requires a Git project")
    _expect_keys(
        request,
        {
            "protocol_version", "operation", "project_path", "project_id", "tree_id",
            "actor_topic_id", "actor_conversation_ref", "expected_ledger_revision",
            "expected_topic_revision", "idempotency_key", "checkpoint_id",
            "expected_checkpoint_revision", "repository_coordination_lease",
        },
        "publish-git-checkpoint request",
    )
    _validate_uuid4(request["idempotency_key"], "idempotency_key")
    _verify_repository_coordination_lease(request["repository_coordination_lease"], project=project, owner_ref=owner_ref)
    with lock_path.open("a+b") as lock_stream:
        _flock_with_timeout(lock_stream)
        frontmatter, records = _load_records(ledger_path)
        replay = _idempotent_result(records, request)
        if replay is not None:
            return replay
        topic_record = _record_by_id(records["Current Topics"], "topic_id", request["actor_topic_id"], "topic_id")
        ledger_revision, topic_revision = _validate_revisions(request, frontmatter, topic_record)
        _verify_topic_owner(records, request["actor_topic_id"], owner_ref)
        record = _checkpoint_record(records, _expect_string(request["checkpoint_id"], "checkpoint_id"))
        checkpoint = _checkpoint_data(record)
        if request["expected_checkpoint_revision"] != checkpoint["record_revision"] or checkpoint["state"] != "prepared":
            raise ProtocolError(
                "checkpoint_identity_conflict",
                "checkpoint is not the expected prepared intent",
                context=_checkpoint_authority_context(request, ledger_revision, checkpoint),
            )
        paths = json.loads(checkpoint["paths_json"])
        digests = json.loads(checkpoint["document_digests_json"])
        current = _require_regular_nosymlink(topic_path, "topic document")
        if _sha256(current) != digests[paths[0]]:
            raise ProtocolError("checkpoint_changed_draft", "topic document bytes differ from the frozen checkpoint intent")
        if _checkpoint_decision_digest(records, request["actor_topic_id"]) != checkpoint["decision_digest"]:
            raise ProtocolError("checkpoint_changed_draft", "topic decisions differ from the frozen checkpoint intent")
        base_commit = _verify_git_base(project, checkpoint["base_ref"])
        if base_commit != checkpoint["base_commit"]:
            raise ProtocolError("checkpoint_base_invalid", "checkpoint base_ref no longer resolves to the frozen base commit")
        blobs = json.loads(checkpoint["blob_ids_json"])
        tree_id = _create_checkpoint_tree(project, base_commit, blobs)
        _inject_failure("git-before-commit-create")
        commit_id = _git(
            project,
            ["commit-tree", tree_id, "-p", base_commit],
            input_bytes=_checkpoint_commit_message(checkpoint).encode("utf-8"),
        ).decode("ascii").strip()
        verified = _commit_matches_checkpoint(project, commit_id, checkpoint)
        if verified is None:
            raise ProtocolError("checkpoint_history_mismatch", "new checkpoint commit did not fully verify")
        checkpoint_ref = (
            "refs/codex/design-discussion/checkpoints/"
            + checkpoint["checkpoint_id"].lower()
        )
        _git(project, ["update-ref", checkpoint_ref, commit_id])
        _inject_failure("git-after-commit-before-result-record")
        checkpoint["state"] = "completed"
        checkpoint["record_revision"] += 1
        checkpoint["published_identity"] = commit_id
        checkpoint["tree_id"] = tree_id
        checkpoint["checkpoint_ref"] = checkpoint_ref
        _store_checkpoint(record, checkpoint)
        next_revision = ledger_revision + 1
        result = {
            "ok": True, "state": "completed", "idempotent_replay": False,
            "project_id": request["project_id"], "tree_id": request["tree_id"], "topic_id": request["actor_topic_id"],
            "ledger_revision": next_revision, "record_revision": topic_revision,
            "checkpoint_id": checkpoint["checkpoint_id"], "checkpoint_record_revision": checkpoint["record_revision"],
            "commit_id": commit_id, "checkpoint_tree_id": tree_id, "paths_verified": paths,
            "checkpoint_ref": checkpoint_ref,
            "stage_entry_phase": checkpoint.get("stage_entry_phase"),
            "stage_entry_phase_result_id": checkpoint.get("stage_entry_phase_result_id"),
        }
        _write_ledger_transaction(ledger_path, frontmatter, records, request, ledger_revision=next_revision, event_type="git-checkpoint-published", result=result)
        return result


def _record_checkpoint_outcome_unknown(request: dict[str, Any]) -> dict[str, Any]:
    project, ledger_path, _, lock_path, owner_ref, storage_kind = _checkpoint_paths(request)
    _expect_keys(
        request,
        {
            "protocol_version", "operation", "project_path", "project_id", "tree_id",
            "actor_topic_id", "actor_conversation_ref", "expected_ledger_revision",
            "expected_topic_revision", "idempotency_key", "checkpoint_id",
            "expected_checkpoint_revision",
        },
        "record-checkpoint-outcome-unknown request",
    )
    _validate_uuid4(request["idempotency_key"], "idempotency_key")
    with lock_path.open("a+b") as lock_stream:
        _flock_with_timeout(lock_stream)
        frontmatter, records = _load_records(ledger_path)
        replay = _idempotent_result(records, request)
        if replay is not None:
            return replay
        topic_record = _record_by_id(records["Current Topics"], "topic_id", request["actor_topic_id"], "topic_id")
        ledger_revision, topic_revision = _validate_revisions(request, frontmatter, topic_record)
        _verify_topic_owner(records, request["actor_topic_id"], owner_ref)
        record = _checkpoint_record(records, _expect_string(request["checkpoint_id"], "checkpoint_id"))
        checkpoint = _checkpoint_data(record)
        if request["expected_checkpoint_revision"] != checkpoint["record_revision"] or checkpoint["state"] != "prepared":
            raise ProtocolError("checkpoint_identity_conflict", "only the expected prepared checkpoint can become outcome-unknown")
        checkpoint["state"] = "outcome-unknown"
        checkpoint["record_revision"] += 1
        _store_checkpoint(record, checkpoint)
        next_revision = ledger_revision + 1
        result = {
            "ok": True, "state": "outcome-unknown", "idempotent_replay": False,
            "project_id": request["project_id"], "tree_id": request["tree_id"], "topic_id": request["actor_topic_id"],
            "ledger_revision": next_revision, "record_revision": topic_revision,
            "checkpoint_id": checkpoint["checkpoint_id"], "checkpoint_record_revision": checkpoint["record_revision"],
            "reconcile_required": True, "storage_kind": storage_kind,
        }
        _write_ledger_transaction(ledger_path, frontmatter, records, request, ledger_revision=next_revision, event_type="checkpoint-outcome-unknown", result=result)
        return result


def _reconcile_git_checkpoint(request: dict[str, Any]) -> dict[str, Any]:
    project, ledger_path, _, lock_path, owner_ref, storage_kind = _checkpoint_paths(request)
    if storage_kind != "git":
        raise ProtocolError("checkpoint_identity_conflict", "Git reconciliation requires a Git project")
    _expect_keys(
        request,
        {
            "protocol_version", "operation", "project_path", "project_id", "tree_id",
            "actor_topic_id", "actor_conversation_ref", "expected_ledger_revision",
            "expected_topic_revision", "idempotency_key", "checkpoint_id",
            "expected_checkpoint_revision",
        },
        "reconcile-git-checkpoint request",
    )
    _validate_uuid4(request["idempotency_key"], "idempotency_key")
    with lock_path.open("a+b") as lock_stream:
        _flock_with_timeout(lock_stream)
        frontmatter, records = _load_records(ledger_path)
        replay = _idempotent_result(records, request)
        if replay is not None:
            return replay
        topic_record = _record_by_id(records["Current Topics"], "topic_id", request["actor_topic_id"], "topic_id")
        ledger_revision, topic_revision = _validate_revisions(request, frontmatter, topic_record)
        _verify_topic_owner(records, request["actor_topic_id"], owner_ref)
        record = _checkpoint_record(records, _expect_string(request["checkpoint_id"], "checkpoint_id"))
        checkpoint = _checkpoint_data(record)
        if request["expected_checkpoint_revision"] != checkpoint["record_revision"] or checkpoint["state"] != "outcome-unknown":
            raise ProtocolError("checkpoint_identity_conflict", "checkpoint is not awaiting Git reconciliation")
        matches = _all_checkpoint_candidates(project, checkpoint)
        if len(matches) > 1:
            raise ProtocolError("checkpoint_history_ambiguous", "more than one commit fully matches the frozen checkpoint")
        next_state = "completed" if matches else "prepared"
        checkpoint["state"] = next_state
        checkpoint["record_revision"] += 1
        if matches:
            checkpoint["published_identity"] = matches[0]["commit_id"]
            checkpoint["tree_id"] = matches[0]["tree_id"]
            checkpoint_ref = (
                "refs/codex/design-discussion/checkpoints/"
                + checkpoint["checkpoint_id"].lower()
            )
            _git(project, ["update-ref", checkpoint_ref, matches[0]["commit_id"]])
            checkpoint["checkpoint_ref"] = checkpoint_ref
        _store_checkpoint(record, checkpoint)
        next_revision = ledger_revision + 1
        result = {
            "ok": True, "state": next_state, "idempotent_replay": False,
            "project_id": request["project_id"], "tree_id": request["tree_id"], "topic_id": request["actor_topic_id"],
            "ledger_revision": next_revision, "record_revision": topic_revision,
            "checkpoint_id": checkpoint["checkpoint_id"], "checkpoint_record_revision": checkpoint["record_revision"],
            "commit_id": matches[0]["commit_id"] if matches else None,
            "retry_allowed": not matches, "match_count": len(matches),
            "checkpoint_ref": checkpoint.get("checkpoint_ref"),
        }
        _write_ledger_transaction(ledger_path, frontmatter, records, request, ledger_revision=next_revision, event_type="git-checkpoint-reconciled", result=result)
        return result


def _publish_non_git_checkpoint(request: dict[str, Any]) -> dict[str, Any]:
    project, ledger_path, topic_path, lock_path, owner_ref, storage_kind = _checkpoint_paths(request)
    if storage_kind != "non-git":
        raise ProtocolError("checkpoint_identity_conflict", "snapshot publication requires a non-Git project")
    _expect_keys(
        request,
        {
            "protocol_version", "operation", "project_path", "project_id", "tree_id",
            "actor_topic_id", "actor_conversation_ref", "expected_ledger_revision",
            "expected_topic_revision", "idempotency_key", "checkpoint_id",
            "expected_checkpoint_revision",
        },
        "publish-non-git-checkpoint request",
    )
    _validate_uuid4(request["idempotency_key"], "idempotency_key")
    with lock_path.open("a+b") as lock_stream:
        _flock_with_timeout(lock_stream)
        frontmatter, records = _load_records(ledger_path)
        replay = _idempotent_result(records, request)
        if replay is not None:
            return replay
        topic_record = _record_by_id(records["Current Topics"], "topic_id", request["actor_topic_id"], "topic_id")
        ledger_revision, topic_revision = _validate_revisions(request, frontmatter, topic_record)
        _verify_topic_owner(records, request["actor_topic_id"], owner_ref)
        active_gc = _active_checkpoint_gc(records)
        if active_gc is not None:
            raise ProtocolError(
                "checkpoint_gc_reconciliation_required",
                "checkpoint GC must be reconciled before publishing a snapshot",
                context={"gc_operation_id": active_gc["result_id"]},
            )
        record = _checkpoint_record(records, _expect_string(request["checkpoint_id"], "checkpoint_id"))
        checkpoint = _checkpoint_data(record)
        if request["expected_checkpoint_revision"] != checkpoint["record_revision"] or checkpoint["state"] != "prepared":
            raise ProtocolError(
                "checkpoint_identity_conflict",
                "checkpoint is not the expected prepared intent",
                context=_checkpoint_authority_context(request, ledger_revision, checkpoint),
            )
        paths = json.loads(checkpoint["paths_json"])
        digests = json.loads(checkpoint["document_digests_json"])
        document = _require_regular_nosymlink(topic_path, "topic document")
        if _sha256(document) != digests[paths[0]]:
            raise ProtocolError("checkpoint_changed_draft", "topic document bytes differ from the frozen checkpoint intent")
        if _checkpoint_decision_digest(records, request["actor_topic_id"]) != checkpoint["decision_digest"]:
            raise ProtocolError("checkpoint_changed_draft", "topic decisions differ from the frozen checkpoint intent")
        snapshot_bytes = base64.b64decode(checkpoint["snapshot_bytes_b64"], validate=True)
        snapshot_digest = checkpoint["snapshot_digest"]
        if _sha256(snapshot_bytes) != snapshot_digest:
            raise ProtocolError("state_corrupt", "frozen snapshot bytes do not match their prepared digest")
        snapshot_path = ledger_path.parents[4] / "checkpoints" / "sha256" / snapshot_digest[:2] / snapshot_digest
        snapshot_path.parent.mkdir(parents=True, exist_ok=True)
        _inject_failure("snapshot-before-create")
        if snapshot_path.exists():
            try:
                existing = _require_regular_nosymlink(
                    snapshot_path, "checkpoint snapshot"
                )
            except ProtocolError as error:
                raise ProtocolError(
                    "checkpoint_snapshot_corrupt",
                    "existing content-addressed snapshot is not a safe immutable object",
                    cause=error.message,
                ) from error
            if _sha256(existing) != snapshot_digest or existing != snapshot_bytes:
                raise ProtocolError("checkpoint_snapshot_corrupt", "existing content-addressed snapshot does not match its digest")
            reused = True
        else:
            _atomic_replace(snapshot_path, snapshot_bytes, mode=0o400)
            snapshot_path.chmod(0o400)
            reused = False
        _inject_failure("snapshot-after-create-before-result-record")
        checkpoint["state"] = "completed"
        checkpoint["record_revision"] += 1
        checkpoint["published_identity"] = snapshot_digest
        checkpoint["snapshot_path"] = str(snapshot_path)
        _store_checkpoint(record, checkpoint)
        next_revision = ledger_revision + 1
        result = {
            "ok": True, "state": "completed", "idempotent_replay": False,
            "project_id": request["project_id"], "tree_id": request["tree_id"], "topic_id": request["actor_topic_id"],
            "ledger_revision": next_revision, "record_revision": topic_revision,
            "checkpoint_id": checkpoint["checkpoint_id"], "checkpoint_record_revision": checkpoint["record_revision"],
            "snapshot_digest": snapshot_digest, "snapshot_path": str(snapshot_path), "snapshot_reused": reused,
            "stage_entry_phase": checkpoint.get("stage_entry_phase"),
            "stage_entry_phase_result_id": checkpoint.get("stage_entry_phase_result_id"),
        }
        _write_ledger_transaction(ledger_path, frontmatter, records, request, ledger_revision=next_revision, event_type="non-git-checkpoint-published", result=result)
        return result


def _reconcile_non_git_checkpoint(request: dict[str, Any]) -> dict[str, Any]:
    project, ledger_path, topic_path, lock_path, owner_ref, storage_kind = _checkpoint_paths(request)
    if storage_kind != "non-git":
        raise ProtocolError("checkpoint_identity_conflict", "snapshot reconciliation requires a non-Git project")
    _expect_keys(
        request,
        {
            "protocol_version", "operation", "project_path", "project_id", "tree_id",
            "actor_topic_id", "actor_conversation_ref", "expected_ledger_revision",
            "expected_topic_revision", "idempotency_key", "checkpoint_id",
            "expected_checkpoint_revision",
        },
        "reconcile-non-git-checkpoint request",
    )
    _validate_uuid4(request["idempotency_key"], "idempotency_key")
    with lock_path.open("a+b") as lock_stream:
        _flock_with_timeout(lock_stream)
        frontmatter, records = _load_records(ledger_path)
        replay = _idempotent_result(records, request)
        if replay is not None:
            return replay
        topic_record = _record_by_id(records["Current Topics"], "topic_id", request["actor_topic_id"], "topic_id")
        ledger_revision, topic_revision = _validate_revisions(request, frontmatter, topic_record)
        _verify_topic_owner(records, request["actor_topic_id"], owner_ref)
        record = _checkpoint_record(records, request["checkpoint_id"])
        checkpoint = _checkpoint_data(record)
        if request["expected_checkpoint_revision"] != checkpoint["record_revision"] or checkpoint["state"] != "outcome-unknown":
            raise ProtocolError("checkpoint_identity_conflict", "checkpoint is not awaiting snapshot reconciliation")
        snapshot_bytes = base64.b64decode(checkpoint["snapshot_bytes_b64"], validate=True)
        snapshot_digest = checkpoint["snapshot_digest"]
        if _sha256(snapshot_bytes) != snapshot_digest:
            raise ProtocolError("state_corrupt", "frozen snapshot bytes do not match their prepared digest")
        snapshot_path = ledger_path.parents[4] / "checkpoints" / "sha256" / snapshot_digest[:2] / snapshot_digest
        if snapshot_path.exists():
            existing = _require_regular_nosymlink(snapshot_path, "checkpoint snapshot")
            if existing != snapshot_bytes:
                raise ProtocolError("checkpoint_snapshot_corrupt", "uncertain snapshot object does not match frozen bytes")
            next_state = "completed"
            checkpoint["published_identity"] = snapshot_digest
            checkpoint["snapshot_path"] = str(snapshot_path)
        else:
            next_state = "prepared"
        checkpoint["state"] = next_state
        checkpoint["record_revision"] += 1
        _store_checkpoint(record, checkpoint)
        next_revision = ledger_revision + 1
        result = {
            "ok": True, "state": next_state, "idempotent_replay": False,
            "project_id": request["project_id"], "tree_id": request["tree_id"], "topic_id": request["actor_topic_id"],
            "ledger_revision": next_revision, "record_revision": topic_revision,
            "checkpoint_id": checkpoint["checkpoint_id"], "checkpoint_record_revision": checkpoint["record_revision"],
            "snapshot_digest": snapshot_digest if next_state == "completed" else None,
            "snapshot_path": str(snapshot_path), "retry_allowed": next_state == "prepared",
        }
        _write_ledger_transaction(ledger_path, frontmatter, records, request, ledger_revision=next_revision, event_type="non-git-checkpoint-reconciled", result=result)
        return result


def _mark_checkpoint_broken(request: dict[str, Any]) -> dict[str, Any]:
    project, ledger_path, _, lock_path, owner_ref, _ = _checkpoint_paths(request)
    _expect_keys(
        request,
        {
            "protocol_version", "operation", "project_path", "project_id", "tree_id",
            "actor_topic_id", "actor_conversation_ref", "expected_ledger_revision",
            "expected_topic_revision", "idempotency_key", "checkpoint_id",
            "expected_checkpoint_revision", "broken_identity", "reason",
        },
        "mark-checkpoint-broken request",
    )
    _validate_uuid4(request["idempotency_key"], "idempotency_key")
    broken_identity = _expect_string(request["broken_identity"], "broken_identity", max_bytes=512)
    reason = _expect_string(request["reason"], "reason", max_bytes=2048)
    with lock_path.open("a+b") as lock_stream:
        _flock_with_timeout(lock_stream)
        frontmatter, records = _load_records(ledger_path)
        replay = _idempotent_result(records, request)
        if replay is not None:
            return replay
        topic_record = _record_by_id(records["Current Topics"], "topic_id", request["actor_topic_id"], "topic_id")
        ledger_revision, topic_revision = _validate_revisions(request, frontmatter, topic_record)
        _verify_topic_owner(records, request["actor_topic_id"], owner_ref)
        record = _checkpoint_record(records, request["checkpoint_id"])
        checkpoint = _checkpoint_data(record)
        if request["expected_checkpoint_revision"] != checkpoint["record_revision"] or checkpoint["state"] != "completed" or checkpoint["published_identity"] != broken_identity:
            raise ProtocolError("checkpoint_identity_conflict", "broken checkpoint fact does not match the completed record")
        checkpoint["state"] = "broken"
        checkpoint["record_revision"] += 1
        checkpoint["broken_identity"] = broken_identity
        checkpoint["break_reason"] = reason
        _store_checkpoint(record, checkpoint)
        next_revision = ledger_revision + 1
        result = {
            "ok": True, "state": "broken", "idempotent_replay": False,
            "project_id": request["project_id"], "tree_id": request["tree_id"], "topic_id": request["actor_topic_id"],
            "ledger_revision": next_revision, "record_revision": topic_revision,
            "checkpoint_id": checkpoint["checkpoint_id"], "checkpoint_record_revision": checkpoint["record_revision"],
            "broken_identity": broken_identity, "original_fact_preserved": True,
        }
        _write_ledger_transaction(ledger_path, frontmatter, records, request, ledger_revision=next_revision, event_type="checkpoint-broken", result=result)
        return result


def _register_active_checkpoint_source(request: dict[str, Any]) -> dict[str, Any]:
    project, ledger_path, _, lock_path, owner_ref, _ = _checkpoint_paths(request)
    _expect_keys(
        request,
        {
            "protocol_version", "operation", "project_path", "project_id", "tree_id",
            "actor_topic_id", "actor_conversation_ref", "expected_ledger_revision",
            "expected_topic_revision", "idempotency_key", "checkpoint_id",
            "expected_checkpoint_revision", "implementation_id",
        },
        "register-active-checkpoint-source request",
    )
    _validate_uuid4(request["idempotency_key"], "idempotency_key")
    implementation_id = _expect_string(
        request["implementation_id"], "implementation_id", max_bytes=256
    )
    with lock_path.open("a+b") as lock_stream:
        _flock_with_timeout(lock_stream)
        frontmatter, records = _load_records(ledger_path)
        replay = _idempotent_result(records, request)
        if replay is not None:
            return replay
        topic_record = _record_by_id(records["Current Topics"], "topic_id", request["actor_topic_id"], "topic_id")
        ledger_revision, topic_revision = _validate_revisions(request, frontmatter, topic_record)
        _verify_topic_owner(records, request["actor_topic_id"], owner_ref)
        checkpoint_record = _checkpoint_record(records, request["checkpoint_id"])
        checkpoint = _checkpoint_data(checkpoint_record)
        if (
            request["expected_checkpoint_revision"] != checkpoint["record_revision"]
            or checkpoint["state"] != "completed"
        ):
            raise ProtocolError("checkpoint_identity_conflict", "active implementation source must be a completed checkpoint")
        if any(
            item.get("implementation_id") == implementation_id
            for item in records["Dependencies and Active Implementations"]
        ):
            raise ProtocolError("checkpoint_identity_conflict", "implementation_id already exists")
        records["Dependencies and Active Implementations"].append(
            {
                "implementation_id": implementation_id,
                "source_checkpoint_id": checkpoint["checkpoint_id"],
                "source_identity": checkpoint["published_identity"],
                "state": "active",
            }
        )
        next_revision = ledger_revision + 1
        result = {
            "ok": True, "state": "active", "idempotent_replay": False,
            "project_id": request["project_id"], "tree_id": request["tree_id"], "topic_id": request["actor_topic_id"],
            "ledger_revision": next_revision, "record_revision": topic_revision,
            "checkpoint_id": checkpoint["checkpoint_id"], "checkpoint_record_revision": checkpoint["record_revision"],
            "implementation_id": implementation_id, "source_identity": checkpoint["published_identity"],
        }
        _write_ledger_transaction(ledger_path, frontmatter, records, request, ledger_revision=next_revision, event_type="active-checkpoint-source-registered", result=result)
        return result


def _implementation_record(
    records: dict[str, list[dict[str, Any]]], implementation_id: str
) -> dict[str, Any]:
    return _record_by_id(
        records["Dependencies and Active Implementations"],
        "implementation_id",
        implementation_id,
        "implementation_id",
    )


def _implementation_data(record: dict[str, Any]) -> dict[str, Any]:
    return _json_field(record, "data_json", "implementation run")


def _implementation_authority(
    records: dict[str, list[dict[str, Any]]],
    request: dict[str, Any],
    implementation_id: Any | None = None,
) -> tuple[dict[str, Any], dict[str, Any]]:
    identity = _expect_string(
        request["implementation_id"] if implementation_id is None else implementation_id,
        "implementation_id",
    )
    record = _implementation_record(records, identity)
    data = _implementation_data(record)
    expected = {
        "project_id": request["project_id"],
        "tree_id": request["tree_id"],
        "topic_id": request["actor_topic_id"],
    }
    observed = {
        "project_id": data.get("project_id"),
        "tree_id": data.get("tree_id"),
        "topic_id": data.get("topic_id"),
    }
    if observed != expected or record.get("topic_id") != expected["topic_id"]:
        raise ProtocolError(
            "implementation_identity_conflict",
            "implementation project, tree, and topic authority does not match the actor",
        )
    return record, data


def _store_implementation(record: dict[str, Any], data: dict[str, Any]) -> None:
    record.update(
        {
            "topic_id": data["topic_id"],
            "state": data["state"],
            "record_revision": data["record_revision"],
            "data_json": _canonical_json(data),
        }
    )


def _normalized_string_set(value: Any, label: str) -> list[str]:
    if not isinstance(value, list):
        raise ProtocolError("invalid_request", f"{label} must be a list")
    normalized = sorted({_expect_string(item, f"{label} item", max_bytes=2048) for item in value})
    if len(normalized) != len(value):
        raise ProtocolError("invalid_request", f"{label} must be sorted unique values")
    return normalized


def _normalize_implementation_scope(value: Any, label: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ProtocolError("invalid_request", f"{label} must be an object")
    required = {
        "paths", "modules", "interfaces", "database_objects", "dependencies",
        "base_commit", "branch", "worktree_path",
    }
    if set(value) not in {frozenset(required), frozenset(required | {"decision_ids"})}:
        raise ProtocolError(
            "invalid_request",
            f"{label} fields do not match the implementation scope schema; "
            f"missing={sorted(required - set(value))!r}; "
            f"unexpected={sorted(set(value) - required - {'decision_ids'})!r}",
        )
    paths = _normalized_string_set(value["paths"], f"{label}.paths")
    for path in paths:
        candidate = Path(path)
        if candidate.is_absolute() or ".." in candidate.parts or path in {".", ""}:
            raise ProtocolError("invalid_request", f"{label}.paths must be repository-relative")
    base_commit = _expect_string(value["base_commit"], f"{label}.base_commit", max_bytes=128)
    if not re.fullmatch(r"(?:[0-9a-f]{40}|[0-9a-f]{64})", base_commit):
        raise ProtocolError("invalid_request", f"{label}.base_commit must be a Git object ID")
    worktree_path = Path(_expect_string(value["worktree_path"], f"{label}.worktree_path", max_bytes=4096))
    if not worktree_path.is_absolute() or str(worktree_path.resolve(strict=False)) != str(worktree_path):
        raise ProtocolError("invalid_request", f"{label}.worktree_path must be canonical and absolute")
    normalized = {
        "paths": paths,
        "modules": _normalized_string_set(value["modules"], f"{label}.modules"),
        "interfaces": _normalized_string_set(value["interfaces"], f"{label}.interfaces"),
        "database_objects": _normalized_string_set(value["database_objects"], f"{label}.database_objects"),
        "dependencies": _normalized_string_set(value["dependencies"], f"{label}.dependencies"),
        "base_commit": base_commit,
        "branch": _expect_string(value["branch"], f"{label}.branch", max_bytes=1024),
        "worktree_path": str(worktree_path),
    }
    if "decision_ids" in value:
        normalized["decision_ids"] = _normalized_string_set(
            value["decision_ids"], f"{label}.decision_ids"
        )
    return normalized


def _normalize_active_implementations(value: Any) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        raise ProtocolError("invalid_request", "active_implementations must be a list")
    normalized = []
    for index, item in enumerate(value):
        if not isinstance(item, dict):
            raise ProtocolError("invalid_request", f"active_implementations[{index}] must be an object")
        _expect_keys(item, {"implementation_id", "state", "scope"}, f"active_implementations[{index}]")
        normalized.append(
            {
                "implementation_id": _expect_string(item["implementation_id"], f"active_implementations[{index}].implementation_id"),
                "state": _expect_string(item["state"], f"active_implementations[{index}].state"),
                "scope": _normalize_implementation_scope(item["scope"], f"active_implementations[{index}].scope"),
            }
        )
    identities = [item["implementation_id"] for item in normalized]
    if len(set(identities)) != len(identities):
        raise ProtocolError("invalid_request", "active implementation identities must be unique")
    return sorted(normalized, key=lambda item: item["implementation_id"])


def _normalize_active_worktrees(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ProtocolError("invalid_request", "active_worktrees must be an object")
    _expect_keys(value, {"verification_state", "worktrees"}, "active_worktrees")
    verification_state = _expect_string(value["verification_state"], "active_worktrees.verification_state")
    if verification_state not in {"verified", "unknown"}:
        raise ProtocolError("invalid_request", "active_worktrees.verification_state is unsupported")
    if not isinstance(value["worktrees"], list):
        raise ProtocolError("invalid_request", "active_worktrees.worktrees must be a list")
    worktrees = []
    for index, item in enumerate(value["worktrees"]):
        if not isinstance(item, dict):
            raise ProtocolError("invalid_request", f"active_worktrees.worktrees[{index}] must be an object")
        _expect_keys(item, {"path", "branch", "base_commit", "implementation_id"}, f"active_worktrees.worktrees[{index}]")
        path = Path(_expect_string(item["path"], f"active_worktrees.worktrees[{index}].path", max_bytes=4096))
        if not path.is_absolute() or str(path.resolve(strict=False)) != str(path):
            raise ProtocolError("invalid_request", "active worktree paths must be canonical and absolute")
        base_commit = _expect_string(item["base_commit"], f"active_worktrees.worktrees[{index}].base_commit", max_bytes=128)
        if not re.fullmatch(r"(?:[0-9a-f]{40}|[0-9a-f]{64})", base_commit):
            raise ProtocolError("invalid_request", "active worktree base_commit must be a Git object ID")
        worktrees.append(
            {
                "path": str(path),
                "branch": _expect_string(item["branch"], f"active_worktrees.worktrees[{index}].branch", max_bytes=1024),
                "base_commit": base_commit,
                "implementation_id": _expect_string(item["implementation_id"], f"active_worktrees.worktrees[{index}].implementation_id"),
            }
        )
    return {"verification_state": verification_state, "worktrees": sorted(worktrees, key=lambda item: (item["path"], item["implementation_id"]))}


def _authoritative_active_implementations(
    records: dict[str, list[dict[str, Any]]], target_id: str
) -> list[dict[str, Any]]:
    active = []
    for record in records["Dependencies and Active Implementations"]:
        data = _implementation_data(record)
        if data["implementation_id"] == target_id or data.get("state") not in {"active", "integrating", "closing", "refresh-pending", "paused"}:
            continue
        active.append(
            {
                "implementation_id": data["implementation_id"],
                "scope": data["scope"],
                "state": data["state"],
            }
        )
    return sorted(active, key=lambda item: item["implementation_id"])


def _call_supervision(command: str, request: dict[str, Any]) -> dict[str, Any]:
    script = Path(__file__).resolve().parents[2] / "guided-implementation" / "scripts" / "supervision_protocol.py"
    if os.environ.get("CODEX_DISCUSSION_TEST_FAILPOINT") == "delay-supervision":
        print("TEST_SUPERVISION_CALL_STARTED", file=sys.stderr, flush=True)
        time.sleep(0.5)
    with tempfile.NamedTemporaryFile(prefix="discussion-supervision-", suffix=".json", delete=False) as stream:
        path = Path(stream.name)
        stream.write((_canonical_json(request) + "\n").encode("utf-8"))
    try:
        try:
            completed = subprocess.run(
                [sys.executable, str(script), command, "--input", str(path)],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                timeout=10,
            )
        except subprocess.TimeoutExpired as error:
            raise ProtocolError(
                "supervision_receipt_invalid",
                "supervision verifier exceeded the bounded ten second deadline",
                retryable=True,
                cause=str(error),
            ) from error
    finally:
        path.unlink(missing_ok=True)
    try:
        result = json.loads(completed.stdout)
    except (json.JSONDecodeError, UnicodeDecodeError) as error:
        raise ProtocolError("supervision_receipt_invalid", "supervision verifier returned invalid JSON", cause=completed.stderr.decode("utf-8", "replace")[:1024]) from error
    if completed.returncode != 0 or not isinstance(result, dict) or result.get("verified") is not True:
        code = result.get("error", {}).get("code") if isinstance(result, dict) else None
        raise ProtocolError("supervision_receipt_invalid", f"supervision authority rejected receipt; code={code!r}", cause=completed.stderr.decode("utf-8", "replace")[:1024])
    return result


def _verified_worktree_snapshot(request: dict[str, Any], receipt: Any) -> dict[str, Any]:
    value = receipt if isinstance(receipt, dict) else {}
    _expect_keys(value, {"file_bytes", "file_sha256", "path", "version"}, "worktree_receipt")
    verified = _call_supervision(
        "verify-worktree-state-receipt",
        {
            **value,
            "project_id": request["project_id"],
            "repository": request["project_path"],
            "topic_id": request["actor_topic_id"],
            "tree_id": request["tree_id"],
        },
    )
    leases_by_path = {
        item["binding"]["worktree_path"]: item["binding"]["implementation_id"]
        for item in verified["snapshot"]["execution_leases"]
        if item["state"] == "held"
    }
    worktrees = []
    for item in verified["snapshot"]["git_worktrees"]:
        worktrees.append(
            {
                "path": item["path"],
                "branch": item.get("branch", "detached"),
                "base_commit": item["head"],
                "implementation_id": leases_by_path.get(item["path"], "ordinary-checkout"),
            }
        )
    return {"verification_state": "verified", "worktrees": worktrees, "authority": {"receipt_id": verified["receipt_id"], "version": verified["version"]}}


def _paths_overlap(left: list[str], right: list[str]) -> bool:
    return any(
        a == b or a.startswith(b.rstrip("/") + "/") or b.startswith(a.rstrip("/") + "/")
        for a in left for b in right
    )


def _parallelism_evaluation(
    scope: dict[str, Any], active: list[dict[str, Any]], worktrees: dict[str, Any]
) -> tuple[str, list[dict[str, str]], str]:
    snapshot = {"scope": scope, "active_implementations": active, "active_worktrees": worktrees}
    digest = _sha256(_canonical_json(snapshot).encode("utf-8"))
    if worktrees["verification_state"] != "verified":
        return "unknown", [{"dimension": "active_worktrees", "reason": "verification-unavailable"}], digest
    conflicts: list[dict[str, str]] = []
    for implementation in active:
        other = implementation["scope"]
        for dimension in ("paths", "modules", "interfaces", "database_objects", "dependencies"):
            overlap = _paths_overlap(scope[dimension], other[dimension]) if dimension == "paths" else bool(set(scope[dimension]) & set(other[dimension]))
            if overlap:
                conflicts.append({"dimension": dimension, "reason": implementation["implementation_id"]})
        if scope["branch"] == other["branch"]:
            conflicts.append({"dimension": "branch", "reason": implementation["implementation_id"]})
        if scope["worktree_path"] == other["worktree_path"]:
            conflicts.append({"dimension": "active_worktrees", "reason": implementation["implementation_id"]})
        if scope["base_commit"] != other["base_commit"]:
            conflicts.append({"dimension": "base_commit", "reason": "relationship-unknown"})
    for worktree in worktrees["worktrees"]:
        if worktree["branch"] == scope["branch"]:
            conflicts.append({"dimension": "branch", "reason": worktree["implementation_id"]})
        if worktree["path"] == scope["worktree_path"] and worktree["implementation_id"] not in {item["implementation_id"] for item in active}:
            conflicts.append({"dimension": "active_worktrees", "reason": worktree["implementation_id"]})
    if any(item["reason"] == "relationship-unknown" for item in conflicts):
        return "unknown", conflicts, digest
    return ("blocked" if conflicts else "safe"), conflicts, digest


def _prepare_implementation_run(request: dict[str, Any]) -> dict[str, Any]:
    project, ledger_path, _, lock_path, owner_ref = _evolution_paths(
        request, allow_tree_topic=True
    )
    _expect_keys(
        request,
        {"protocol_version", "operation", "project_path", "project_id", "tree_id", "actor_topic_id", "actor_conversation_ref", "expected_ledger_revision", "expected_topic_revision", "idempotency_key", "implementation_id", "scope"},
        "prepare-implementation-run request",
    )
    del project
    _validate_uuid4(request["idempotency_key"], "idempotency_key")
    implementation_id = _expect_string(request["implementation_id"], "implementation_id")
    scope = _normalize_implementation_scope(request["scope"], "scope")
    with lock_path.open("a+b") as lock_stream:
        _flock_with_timeout(lock_stream)
        frontmatter, records = _load_records(ledger_path)
        replay = _idempotent_result(records, request)
        if replay is not None:
            return replay
        topic = _record_by_id(records["Current Topics"], "topic_id", request["actor_topic_id"], "topic_id")
        ledger_revision, topic_revision = _validate_revisions(request, frontmatter, topic)
        _verify_topic_owner(records, request["actor_topic_id"], owner_ref)
        if any(item.get("implementation_id") == implementation_id for item in records["Dependencies and Active Implementations"]):
            raise ProtocolError("implementation_identity_conflict", "implementation_id already exists")
        data = {"implementation_id": implementation_id, "project_id": request["project_id"], "tree_id": request["tree_id"], "topic_id": request["actor_topic_id"], "state": "prepared", "record_revision": 1, "scope": scope, "parallelism": None, "execution": None, "source_refresh": None}
        record = {"implementation_id": implementation_id}
        _store_implementation(record, data)
        records["Dependencies and Active Implementations"].append(record)
        next_revision = ledger_revision + 1
        result = {"ok": True, "state": "prepared", "idempotent_replay": False, "project_id": request["project_id"], "tree_id": request["tree_id"], "topic_id": request["actor_topic_id"], "ledger_revision": next_revision, "record_revision": topic_revision, "implementation_id": implementation_id, "implementation_record_revision": 1, "scope": scope}
        _write_ledger_transaction(ledger_path, frontmatter, records, request, ledger_revision=next_revision, event_type="implementation-run-prepared", result=result)
        return result


def _check_implementation_parallelism(request: dict[str, Any]) -> dict[str, Any]:
    _, ledger_path, _, lock_path, owner_ref = _evolution_paths(
        request, allow_tree_topic=True
    )
    _expect_keys(request, {"protocol_version", "operation", "project_path", "project_id", "tree_id", "actor_topic_id", "actor_conversation_ref", "expected_ledger_revision", "expected_topic_revision", "idempotency_key", "implementation_id", "worktree_receipt"}, "check-implementation-parallelism request")
    _validate_uuid4(request["idempotency_key"], "idempotency_key")
    with lock_path.open("a+b") as lock_stream:
        _flock_with_timeout(lock_stream)
        _, records = _load_records(ledger_path)
        _verify_topic_owner(records, request["actor_topic_id"], owner_ref)
        _implementation_authority(records, request)
    worktrees = _verified_worktree_snapshot(request, request["worktree_receipt"])
    with lock_path.open("a+b") as lock_stream:
        _flock_with_timeout(lock_stream)
        frontmatter, records = _load_records(ledger_path)
        replay = _idempotent_result(records, request)
        if replay is not None:
            return replay
        topic = _record_by_id(records["Current Topics"], "topic_id", request["actor_topic_id"], "topic_id")
        ledger_revision, topic_revision = _validate_revisions(request, frontmatter, topic)
        _verify_topic_owner(records, request["actor_topic_id"], owner_ref)
        record, data = _implementation_authority(records, request)
        active = _authoritative_active_implementations(records, data["implementation_id"])
        verdict, conflicts, receipt = _parallelism_evaluation(data["scope"], active, worktrees)
        data["parallelism"] = {"receipt": receipt, "verdict": verdict, "conflicts": conflicts, "dimensions": IMPLEMENTATION_PARALLELISM_DIMENSIONS}
        data["record_revision"] += 1
        _store_implementation(record, data)
        next_revision = ledger_revision + 1
        result = {"ok": True, "state": "checked", "idempotent_replay": False, "project_id": request["project_id"], "tree_id": request["tree_id"], "topic_id": request["actor_topic_id"], "ledger_revision": next_revision, "record_revision": topic_revision, "implementation_id": data["implementation_id"], "implementation_record_revision": data["record_revision"], "verdict": verdict, "conflicts": conflicts, "dimensions": IMPLEMENTATION_PARALLELISM_DIMENSIONS, "receipt": receipt}
        _write_ledger_transaction(ledger_path, frontmatter, records, request, ledger_revision=next_revision, event_type="implementation-parallelism-checked", result=result)
        return result


def _validate_implementation_parallelism(request: dict[str, Any]) -> dict[str, Any]:
    _, ledger_path, _, lock_path, owner_ref = _evolution_paths(
        request, query=True, allow_tree_topic=True
    )
    _expect_keys(request, {"protocol_version", "operation", "project_path", "project_id", "tree_id", "actor_topic_id", "actor_conversation_ref", "implementation_id", "receipt", "worktree_receipt"}, "validate-implementation-parallelism request")
    with lock_path.open("a+b") as lock_stream:
        _flock_with_timeout(lock_stream)
        _, records = _load_records(ledger_path)
        _verify_topic_owner(records, request["actor_topic_id"], owner_ref)
        _implementation_authority(records, request)
    worktrees = _verified_worktree_snapshot(request, request["worktree_receipt"])
    with lock_path.open("a+b") as lock_stream:
        _flock_with_timeout(lock_stream)
        frontmatter, records = _load_records(ledger_path)
        _verify_topic_owner(records, request["actor_topic_id"], owner_ref)
        _, data = _implementation_authority(records, request)
        active = _authoritative_active_implementations(records, data["implementation_id"])
        verdict, conflicts, receipt = _parallelism_evaluation(data["scope"], active, worktrees)
        supplied = _expect_string(request["receipt"], "receipt", max_bytes=64)
        if supplied != receipt or data.get("parallelism", {}).get("receipt") != receipt:
            raise ProtocolError("implementation_parallelism_stale", "parallelism inputs changed after the recorded check", context={"state": "stale", "ledger_revision": int(frontmatter["ledger_revision"]), "record_revision": data["record_revision"], "project_id": request["project_id"], "tree_id": request["tree_id"], "topic_id": request["actor_topic_id"]})
        return {
            "ok": True,
            "state": "valid",
            "project_path": request["project_path"],
            "project_id": request["project_id"],
            "tree_id": request["tree_id"],
            "topic_id": request["actor_topic_id"],
            "ledger_revision": int(frontmatter["ledger_revision"]),
            "record_revision": data["record_revision"],
            "implementation_id": data["implementation_id"],
            "verdict": verdict,
            "conflicts": conflicts,
            "receipt": receipt,
        }


def _verified_implementation_source(
    records: dict[str, list[dict[str, Any]]],
    request: dict[str, Any],
    checkpoint_id: Any,
    source_identity: Any,
) -> dict[str, Any]:
    checkpoint_record = _checkpoint_record(
        records, _expect_string(checkpoint_id, "source_checkpoint_id", max_bytes=64)
    )
    checkpoint = _checkpoint_data(checkpoint_record)
    identity = _expect_string(source_identity, "source_identity", max_bytes=128)
    if (
        checkpoint.get("state") != "completed"
        or checkpoint.get("storage_kind") != "git"
        or checkpoint.get("purpose") != "implementation-source"
        or checkpoint.get("published_identity") != identity
        or checkpoint.get("project_id") != request["project_id"]
        or checkpoint.get("authority_tree_id") != request["tree_id"]
        or checkpoint.get("topic_id") != request["actor_topic_id"]
        or checkpoint_record.get("topic_id") != request["actor_topic_id"]
    ):
        raise ProtocolError(
            "implementation_source_invalid",
            "implementation source is not the exact completed Git implementation-source checkpoint",
        )
    _checkpoint_source_authority(checkpoint)
    return checkpoint


def _implementation_decision_ids(
    source_authority: dict[str, Any],
    declared: list[str] | None = None,
) -> list[str]:
    confirmed = source_authority["confirmed_decision_ids"]
    if declared is None:
        return list(confirmed)
    if any(item not in set(confirmed) for item in declared):
        raise ProtocolError(
            "implementation_identity_conflict",
            "implementation scope decision_ids must identify confirmed source-topic decisions",
        )
    return list(declared)


def _checkpoint_source_authority(checkpoint: dict[str, Any]) -> dict[str, Any]:
    try:
        decision_digests = json.loads(checkpoint["decision_digests_json"])
        confirmed_decision_ids = json.loads(checkpoint["confirmed_decision_ids_json"])
        source_paths = json.loads(checkpoint["paths_json"])
    except (KeyError, TypeError, json.JSONDecodeError) as error:
        raise ProtocolError(
            "implementation_source_invalid",
            "implementation source checkpoint lacks reconstructable decision/scope authority",
        ) from error
    if (
        not isinstance(decision_digests, dict)
        or any(
            not isinstance(key, str)
            or not isinstance(value, str)
            or not SHA256_RE.fullmatch(value)
            for key, value in decision_digests.items()
        )
        or not isinstance(confirmed_decision_ids, list)
        or any(
            not isinstance(item, str) or item not in decision_digests
            for item in confirmed_decision_ids
        )
        or confirmed_decision_ids != sorted(set(confirmed_decision_ids))
        or not isinstance(source_paths, list)
        or any(not isinstance(item, str) for item in source_paths)
        or _sha256(_canonical_json(source_paths).encode("utf-8"))
        != checkpoint.get("path_set_digest")
    ):
        raise ProtocolError(
            "implementation_source_invalid",
            "implementation source checkpoint decision/scope authority is corrupt",
        )
    return {
        "checkpoint_id": checkpoint["checkpoint_id"],
        "source_identity": checkpoint["published_identity"],
        "project_id": checkpoint["project_id"],
        "tree_id": checkpoint["authority_tree_id"],
        "topic_id": checkpoint["topic_id"],
        "decision_ids": sorted(decision_digests),
        "decision_digests": decision_digests,
        "confirmed_decision_ids": confirmed_decision_ids,
        "source_scope_digest": checkpoint["path_set_digest"],
        "declared_source_scope": source_paths,
    }


def _topic_impact_authority(
    records: dict[str, list[dict[str, Any]]], topic_id: str
) -> tuple[list[dict[str, Any]], dict[str, str]]:
    impacts = sorted(
        (
            _json_field(record, "data_json", "impact")
            for record in records["Impacts"]
            if record.get("topic_id") == topic_id
        ),
        key=lambda item: item["impact_id"],
    )
    return impacts, {
        item["impact_id"]: _sha256(_canonical_json(item).encode("utf-8"))
        for item in impacts
    }


def _authoritative_source_impact(
    records: dict[str, list[dict[str, Any]]],
    topic_id: str,
    *,
    baseline_authority: dict[str, Any],
    candidate_authority: dict[str, Any],
    decision_ids: list[str],
    scope: dict[str, Any],
) -> tuple[str, str, list[str]]:
    decision_set = set(decision_ids)
    baseline_decisions = baseline_authority["decision_digests"]
    candidate_decisions = candidate_authority["decision_digests"]
    changed_decision_ids = sorted(
        decision_id
        for decision_id in set(baseline_decisions) | set(candidate_decisions)
        if baseline_decisions.get(decision_id) != candidate_decisions.get(decision_id)
    )
    related_changed = sorted(decision_set & set(changed_decision_ids))
    impacts, impact_digests = _topic_impact_authority(records, topic_id)
    baseline_impacts = baseline_authority.get("impact_digests", {})
    changed_impacts = [
        item
        for item in impacts
        if impact_digests[item["impact_id"]]
        != baseline_impacts.get(item["impact_id"])
    ]
    related_impacts = [
        item for item in changed_impacts if item.get("decision_id") in decision_set
    ]
    pending = [
        item["impact_id"]
        for item in related_impacts
        if item.get("state") in {"pending", "unknown"}
    ]
    affected = [
        item["impact_id"]
        for item in related_impacts
        if item.get("state") == "resolved"
        and item.get("action") in {"adjust", "replace", "discard"}
    ]
    declared_scope_digest = _sha256(_canonical_json(scope).encode("utf-8"))
    scope_changed = (
        candidate_authority["source_scope_digest"]
        != baseline_authority["source_scope_digest"]
        or declared_scope_digest != baseline_authority["declared_scope_digest"]
    )
    assessment = {
        "affected_impact_ids": affected,
        "candidate_source_authority": candidate_authority,
        "changed_decision_ids": changed_decision_ids,
        "changed_impact_ids": [item["impact_id"] for item in changed_impacts],
        "declared_scope_digest": declared_scope_digest,
        "pending_impact_ids": pending,
        "related_changed_decision_ids": related_changed,
        "scope_changed": scope_changed,
    }
    digest = _sha256(_canonical_json(assessment).encode("utf-8"))
    if pending:
        return "unknown", digest, pending
    if affected:
        return "affected", digest, affected
    if related_changed or scope_changed:
        return "unknown", digest, related_changed
    return "no-impact", digest, []


def _validate_isolated_confirmation(
    request: dict[str, Any], value: Any, scope: dict[str, Any], receipt: str
) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ProtocolError("isolated_worktree_confirmation_required", "isolated_confirmation must be a supervision receipt")
    _expect_keys(value, {"file_bytes", "file_sha256", "path", "version"}, "isolated_confirmation")
    binding = {
        "base_commit": scope["base_commit"],
        "implementation_branch": scope["branch"],
        "implementation_id": request["implementation_id"],
        "phase_run_id": request["phase_run_id"],
        "repository": request["project_path"],
        "scope_sha256": _sha256(_canonical_json(scope).encode("utf-8")),
        "sensitive_shared_surfaces": request["sensitive_shared_surfaces"],
        "topic_id": request["actor_topic_id"],
        "worktree_path": scope["worktree_path"],
    }
    verified = _call_supervision(
        "verify-isolated-worktree-confirmation",
        {"binding": binding, "confirmation": value, "parallelism_receipt": receipt, "repository": request["project_path"]},
    )
    source_task_id = (verified.get("user_decision") or {}).get("payload", {}).get(
        "source_task_id"
    )
    if source_task_id != request["source_task_id"]:
        raise ProtocolError(
            "isolated_worktree_confirmation_required",
            "isolated confirmation source task does not match activation authority",
        )
    return {
        "binding": binding,
        "file_sha256": verified["file_sha256"],
        "path": value["path"],
        "source_task_id": source_task_id,
        "user_decision": verified["user_decision"],
        "version": value["version"],
    }


def _activate_implementation_run(request: dict[str, Any]) -> dict[str, Any]:
    _, ledger_path, _, lock_path, owner_ref = _evolution_paths(
        request, allow_tree_topic=True
    )
    _expect_keys(
        request,
        {"protocol_version", "operation", "project_path", "project_id", "tree_id", "actor_topic_id", "actor_conversation_ref", "expected_ledger_revision", "expected_topic_revision", "idempotency_key", "implementation_id", "execution_mode", "source_checkpoint_id", "source_identity", "parallelism_receipt", "worktree_receipt", "isolated_confirmation", "phase_run_id", "sensitive_shared_surfaces", "source_host_id", "source_task_id"},
        "activate-implementation-run request",
    )
    _validate_uuid4(request["idempotency_key"], "idempotency_key")
    mode = _expect_string(request["execution_mode"], "execution_mode")
    if mode not in {"exclusive-checkout-v2", "isolated-worktree-v1"}:
        raise ProtocolError("invalid_request", "execution_mode is unsupported")
    supplied_receipt = _expect_string(
        request["parallelism_receipt"], "parallelism_receipt", max_bytes=64
    )
    phase_run_id = _expect_string(request["phase_run_id"], "phase_run_id", max_bytes=256)
    source_host_id = _expect_string(request["source_host_id"], "source_host_id", max_bytes=256)
    source_task_id = _expect_string(request["source_task_id"], "source_task_id", max_bytes=256)

    # Snapshot only discussion-owned authority while holding the ledger lock.
    # Cross-protocol verification deliberately happens after this block.
    with lock_path.open("a+b") as lock_stream:
        _flock_with_timeout(lock_stream)
        frontmatter, records = _load_records(ledger_path)
        replay = _idempotent_result(records, request)
        if replay is not None:
            return replay
        topic = _record_by_id(records["Current Topics"], "topic_id", request["actor_topic_id"], "topic_id")
        ledger_revision, topic_revision = _validate_revisions(request, frontmatter, topic)
        _verify_topic_owner(records, request["actor_topic_id"], owner_ref)
        record, data = _implementation_authority(records, request)
        if data.get("execution") is not None or data.get("state") != "prepared":
            raise ProtocolError("execution_mode_frozen", "activated implementation mode cannot be changed")
        if mode == "exclusive-checkout-v2" and request["isolated_confirmation"] is not None:
            raise ProtocolError("invalid_request", "exclusive checkout activation does not accept isolated confirmation")
        checkpoint = _verified_implementation_source(
            records, request, request["source_checkpoint_id"], request["source_identity"]
        )
        source_authority = _checkpoint_source_authority(checkpoint)
        snapshot = {
            "active": _authoritative_active_implementations(records, data["implementation_id"]),
            "implementation_id": data["implementation_id"],
            "implementation_record_revision": data["record_revision"],
            "parallelism": data.get("parallelism"),
            "scope": data["scope"],
            "source_checkpoint_id": checkpoint["checkpoint_id"],
            "source_identity": checkpoint["published_identity"],
            "source_authority": source_authority,
        }

    worktrees = _verified_worktree_snapshot(request, request["worktree_receipt"])
    verdict, _, receipt = _parallelism_evaluation(
        snapshot["scope"], snapshot["active"], worktrees
    )
    if supplied_receipt != receipt or (snapshot["parallelism"] or {}).get("receipt") != receipt:
        raise ProtocolError(
            "implementation_parallelism_stale",
            "parallelism inputs changed before activation",
        )
    confirmation = None
    if mode == "isolated-worktree-v1":
        if verdict != "safe":
            raise ProtocolError(
                "isolated_worktree_confirmation_required",
                "isolated execution requires a safe parallelism receipt",
            )
        confirmation = _validate_isolated_confirmation(
            request, request["isolated_confirmation"], snapshot["scope"], receipt
        )

    # Commit with the caller's expected revision as the CAS. Any ledger change
    # during external verification fails deterministically before mutation.
    with lock_path.open("a+b") as lock_stream:
        _flock_with_timeout(lock_stream)
        frontmatter, records = _load_records(ledger_path)
        replay = _idempotent_result(records, request)
        if replay is not None:
            return replay
        topic = _record_by_id(records["Current Topics"], "topic_id", request["actor_topic_id"], "topic_id")
        ledger_revision, topic_revision = _validate_revisions(request, frontmatter, topic)
        _verify_topic_owner(records, request["actor_topic_id"], owner_ref)
        record, data = _implementation_authority(
            records, request, snapshot["implementation_id"]
        )
        checkpoint = _verified_implementation_source(
            records, request, request["source_checkpoint_id"], request["source_identity"]
        )
        source_authority = _checkpoint_source_authority(checkpoint)
        current_identity = {
            "active": _authoritative_active_implementations(records, data["implementation_id"]),
            "implementation_id": data["implementation_id"],
            "implementation_record_revision": data["record_revision"],
            "parallelism": data.get("parallelism"),
            "scope": data["scope"],
            "source_checkpoint_id": checkpoint["checkpoint_id"],
            "source_identity": checkpoint["published_identity"],
            "source_authority": source_authority,
        }
        if current_identity != snapshot or data.get("execution") is not None or data.get("state") != "prepared":
            raise ProtocolError(
                "implementation_identity_conflict",
                "implementation authority changed during external verification",
            )
        decision_ids = _implementation_decision_ids(
            source_authority,
            data["scope"].get("decision_ids"),
        )
        _, impact_digests = _topic_impact_authority(
            records, request["actor_topic_id"]
        )
        source_authority = {
            **source_authority,
            "declared_scope_digest": _sha256(
                _canonical_json(data["scope"]).encode("utf-8")
            ),
            "declared_scope_identity": data["scope"],
            "impact_digests": impact_digests,
        }
        data["state"] = "active"
        data["record_revision"] += 1
        data["execution"] = {
            "mode": mode,
            "phase_run_id": phase_run_id,
            "source_host_id": source_host_id,
            "source_task_id": source_task_id,
            "source_checkpoint_id": checkpoint["checkpoint_id"],
            "source_identity": checkpoint["published_identity"],
            "parallelism_receipt": receipt,
            "explicit_confirmation": confirmation,
            "decision_ids": decision_ids,
            "source_authority": source_authority,
            "impact_receipt": _sha256(
                _canonical_json(source_authority).encode("utf-8")
            ),
        }
        _store_implementation(record, data)
        next_revision = ledger_revision + 1
        result = {"ok": True, "state": "active", "idempotent_replay": False, "project_id": request["project_id"], "tree_id": request["tree_id"], "topic_id": request["actor_topic_id"], "ledger_revision": next_revision, "record_revision": topic_revision, "implementation_id": data["implementation_id"], "implementation_record_revision": data["record_revision"], "execution_mode": mode, "source_checkpoint_id": checkpoint["checkpoint_id"], "source_identity": checkpoint["published_identity"], "scope": data["scope"], "explicit_confirmation": confirmation}
        _write_ledger_transaction(ledger_path, frontmatter, records, request, ledger_revision=next_revision, event_type="implementation-run-activated", result=result)
        return result


def _source_refresh_context(request: dict[str, Any], extra: set[str]) -> tuple[Path, Path, str]:
    _, ledger_path, _, lock_path, owner_ref = _evolution_paths(
        request, allow_tree_topic=True
    )
    _expect_keys(
        request,
        {"protocol_version", "operation", "project_path", "project_id", "tree_id", "actor_topic_id", "actor_conversation_ref", "expected_ledger_revision", "expected_topic_revision", "idempotency_key", "implementation_id"} | extra,
        f"{request['operation']} request",
    )
    _validate_uuid4(request["idempotency_key"], "idempotency_key")
    return ledger_path, lock_path, owner_ref


def _prepare_source_refresh(request: dict[str, Any]) -> dict[str, Any]:
    ledger_path, lock_path, owner_ref = _source_refresh_context(request, {"source_checkpoint_id", "source_identity", "impact", "impact_summary"})
    claimed_impact = _expect_string(request["impact"], "impact")
    if claimed_impact not in {"no-impact", "affected", "unknown"}:
        raise ProtocolError("invalid_request", "source refresh impact is unsupported")
    summary = _expect_string(request["impact_summary"], "impact_summary", max_bytes=2048)
    with lock_path.open("a+b") as lock_stream:
        _flock_with_timeout(lock_stream)
        frontmatter, records = _load_records(ledger_path)
        replay = _idempotent_result(records, request)
        if replay is not None:
            return replay
        topic = _record_by_id(records["Current Topics"], "topic_id", request["actor_topic_id"], "topic_id")
        ledger_revision, topic_revision = _validate_revisions(request, frontmatter, topic)
        _verify_topic_owner(records, request["actor_topic_id"], owner_ref)
        record, data = _implementation_authority(records, request)
        if data.get("state") != "active" or data.get("source_refresh") is not None:
            raise ProtocolError("source_refresh_state_conflict", "implementation is not ready for a source refresh candidate")
        checkpoint = _verified_implementation_source(
            records, request, request["source_checkpoint_id"], request["source_identity"]
        )
        if checkpoint["published_identity"] == data["execution"]["source_identity"]:
            raise ProtocolError("source_refresh_state_conflict", "source refresh must advance to a different committed identity")
        candidate_authority = _checkpoint_source_authority(checkpoint)
        current_decision_digests, current_confirmed_decision_ids = (
            _checkpoint_decision_authority(records, request["actor_topic_id"])
        )
        if (
            candidate_authority["decision_digests"] != current_decision_digests
            or candidate_authority["confirmed_decision_ids"]
            != current_confirmed_decision_ids
        ):
            raise ProtocolError(
                "implementation_source_invalid",
                "source refresh checkpoint does not match current ledger decision authority",
            )
        baseline_authority = data["execution"].get("source_authority")
        if not isinstance(baseline_authority, dict):
            raise ProtocolError(
                "implementation_source_invalid",
                "active implementation lacks frozen reconstructable source authority",
            )
        impact, impact_receipt, impact_ids = _authoritative_source_impact(
            records,
            request["actor_topic_id"],
            baseline_authority=baseline_authority,
            candidate_authority=candidate_authority,
            decision_ids=data["execution"].get("decision_ids", []),
            scope=data["scope"],
        )
        if claimed_impact != impact:
            raise ProtocolError("source_refresh_impact_mismatch", f"claimed source impact differs from authoritative ledger; claimed={claimed_impact}; authoritative={impact}", context={"state": "blocked", "ledger_revision": ledger_revision, "record_revision": data["record_revision"], "project_id": request["project_id"], "tree_id": request["tree_id"], "topic_id": request["actor_topic_id"]})
        refresh_id = "REF-" + _sha256(f"{data['implementation_id']}:{checkpoint['checkpoint_id']}".encode("utf-8"))[:16]
        _, current_impact_digests = _topic_impact_authority(
            records, request["actor_topic_id"]
        )
        candidate_authority = {
            **candidate_authority,
            "declared_scope_digest": _sha256(
                _canonical_json(data["scope"]).encode("utf-8")
            ),
            "declared_scope_identity": data["scope"],
            "impact_digests": current_impact_digests,
        }
        refresh = {"refresh_id": refresh_id, "state": "candidate" if impact == "no-impact" else "impact-pending", "impact": impact, "impact_receipt": impact_receipt, "impact_ids": impact_ids, "impact_summary": summary, "source_checkpoint_id": checkpoint["checkpoint_id"], "source_identity": checkpoint["published_identity"], "source_authority": candidate_authority, "implementation_ack": None}
        data["source_refresh"] = refresh
        data["state"] = "refresh-pending" if impact == "no-impact" else "paused"
        data["record_revision"] += 1
        _store_implementation(record, data)
        next_revision = ledger_revision + 1
        state = "refresh-candidate" if impact == "no-impact" else "impact-pending"
        result = {"ok": True, "state": state, "idempotent_replay": False, "project_id": request["project_id"], "tree_id": request["tree_id"], "topic_id": request["actor_topic_id"], "ledger_revision": next_revision, "record_revision": topic_revision, "implementation_id": data["implementation_id"], "implementation_record_revision": data["record_revision"], "refresh_id": refresh_id, "impact": impact, "source_checkpoint_id": checkpoint["checkpoint_id"], "source_identity": checkpoint["published_identity"]}
        _write_ledger_transaction(ledger_path, frontmatter, records, request, ledger_revision=next_revision, event_type="implementation-source-refresh-prepared", result=result)
        return result


def _ack_source_refresh(request: dict[str, Any]) -> dict[str, Any]:
    ledger_path, lock_path, owner_ref = _source_refresh_context(request, {"refresh_id", "implementation_ack"})
    with lock_path.open("a+b") as lock_stream:
        _flock_with_timeout(lock_stream)
        frontmatter, records = _load_records(ledger_path)
        replay = _idempotent_result(records, request)
        if replay is not None:
            return replay
        topic = _record_by_id(records["Current Topics"], "topic_id", request["actor_topic_id"], "topic_id")
        ledger_revision, topic_revision = _validate_revisions(request, frontmatter, topic)
        _verify_topic_owner(records, request["actor_topic_id"], owner_ref)
        record, data = _implementation_authority(records, request)
        refresh = data.get("source_refresh")
        ack = request["implementation_ack"]
        if not isinstance(refresh, dict) or refresh.get("state") != "candidate" or request["refresh_id"] != refresh.get("refresh_id") or not isinstance(ack, dict):
            raise ProtocolError("source_refresh_state_conflict", "source refresh is not awaiting implementation acknowledgement")
        _expect_keys(ack, {"acknowledged", "implementation_id", "source_identity"}, "implementation_ack")
        if ack != {"acknowledged": True, "implementation_id": data["implementation_id"], "source_identity": refresh["source_identity"]}:
            raise ProtocolError("source_refresh_state_conflict", "implementation acknowledgement does not bind the refresh candidate")
        refresh["state"] = "acknowledged"
        refresh["implementation_ack"] = dict(ack)
        data["record_revision"] += 1
        _store_implementation(record, data)
        next_revision = ledger_revision + 1
        result = {"ok": True, "state": "refresh-acknowledged", "idempotent_replay": False, "project_id": request["project_id"], "tree_id": request["tree_id"], "topic_id": request["actor_topic_id"], "ledger_revision": next_revision, "record_revision": topic_revision, "implementation_id": data["implementation_id"], "implementation_record_revision": data["record_revision"], "refresh_id": refresh["refresh_id"]}
        _write_ledger_transaction(ledger_path, frontmatter, records, request, ledger_revision=next_revision, event_type="implementation-source-refresh-acknowledged", result=result)
        return result


def _commit_source_refresh(request: dict[str, Any]) -> dict[str, Any]:
    ledger_path, lock_path, owner_ref = _source_refresh_context(request, {"refresh_id", "source_checkpoint_id", "source_identity"})
    with lock_path.open("a+b") as lock_stream:
        _flock_with_timeout(lock_stream)
        frontmatter, records = _load_records(ledger_path)
        replay = _idempotent_result(records, request)
        if replay is not None:
            return replay
        topic = _record_by_id(records["Current Topics"], "topic_id", request["actor_topic_id"], "topic_id")
        ledger_revision, topic_revision = _validate_revisions(request, frontmatter, topic)
        _verify_topic_owner(records, request["actor_topic_id"], owner_ref)
        record, data = _implementation_authority(records, request)
        refresh = data.get("source_refresh")
        checkpoint = _verified_implementation_source(
            records, request, request["source_checkpoint_id"], request["source_identity"]
        )
        if not isinstance(refresh, dict) or refresh.get("state") != "acknowledged" or request["refresh_id"] != refresh.get("refresh_id") or checkpoint["checkpoint_id"] != refresh.get("source_checkpoint_id") or checkpoint["published_identity"] != refresh.get("source_identity"):
            raise ProtocolError("source_refresh_state_conflict", "source topic commit does not match the acknowledged refresh")
        data["execution"]["source_checkpoint_id"] = checkpoint["checkpoint_id"]
        data["execution"]["source_identity"] = checkpoint["published_identity"]
        data["execution"]["impact_receipt"] = refresh["impact_receipt"]
        data["execution"]["source_authority"] = refresh["source_authority"]
        data["source_refresh"] = None
        data["state"] = "active"
        data["record_revision"] += 1
        _store_implementation(record, data)
        next_revision = ledger_revision + 1
        result = {"ok": True, "state": "active", "idempotent_replay": False, "project_id": request["project_id"], "tree_id": request["tree_id"], "topic_id": request["actor_topic_id"], "ledger_revision": next_revision, "record_revision": topic_revision, "implementation_id": data["implementation_id"], "implementation_record_revision": data["record_revision"], "refresh_id": refresh["refresh_id"], "source_checkpoint_id": checkpoint["checkpoint_id"], "source_identity": checkpoint["published_identity"]}
        _write_ledger_transaction(ledger_path, frontmatter, records, request, ledger_revision=next_revision, event_type="implementation-source-refreshed", result=result)
        return result


def _integration_authority(
    request: dict[str, Any], frontmatter: dict[str, str], records: dict[str, list[dict[str, Any]]]
) -> dict[str, Any]:
    implementation_id = _expect_string(request["implementation_id"], "implementation_id")
    _, target = _implementation_authority(records, request, implementation_id)
    execution = target.get("execution")
    if not isinstance(execution, dict) or target.get("state") not in {"active", "integrating", "closing", "refresh-pending", "paused"}:
        raise ProtocolError("implementation_identity_conflict", "integration authority requires an activated implementation")
    active = []
    for item in _authoritative_active_implementations(records, ""):
        item_data = _implementation_data(_implementation_record(records, item["implementation_id"]))
        item_execution = item_data.get("execution") or {}
        active.append(
            {
                "execution_mode": item_execution.get("mode"),
                "implementation_id": item["implementation_id"],
                "state": item["state"],
                "worktree_path": item["scope"]["worktree_path"],
            }
        )
    dependencies = sorted(
        {
            dependency
            for record in records["Dependencies and Active Implementations"]
            for dependency in (
                _implementation_data(record).get("scope", {}).get("dependencies", [])
                if record.get("implementation_id")
                else []
            )
        }
    )
    body = {
        "active_implementations": active,
        "active_implementations_receipt": _sha256(_canonical_json(active).encode("utf-8")),
        "base_commit": target["scope"]["base_commit"],
        "dependency_receipt": _sha256(_canonical_json(dependencies).encode("utf-8")),
        "implementation_id": implementation_id,
        "implementation_record_revision": target["record_revision"],
        "ledger_revision": int(frontmatter["ledger_revision"]),
        "project_id": request["project_id"],
        "source_identity": execution["source_identity"],
        "topic_id": request["actor_topic_id"],
        "tree_id": request["tree_id"],
    }
    return {**body, "receipt": _sha256(_canonical_json(body).encode("utf-8"))}


def _issue_integration_authority_receipt(request: dict[str, Any]) -> dict[str, Any]:
    _, ledger_path, _, lock_path, owner_ref = _evolution_paths(
        request, query=True, allow_tree_topic=True
    )
    _expect_keys(request, {"protocol_version", "operation", "project_path", "project_id", "tree_id", "actor_topic_id", "actor_conversation_ref", "implementation_id"}, "issue-integration-authority-receipt request")
    with lock_path.open("a+b") as lock_stream:
        _flock_with_timeout(lock_stream)
        frontmatter, records = _load_records(ledger_path)
        _verify_topic_owner(records, request["actor_topic_id"], owner_ref)
        authority = _integration_authority(request, frontmatter, records)
        return {"ok": True, "state": "issued", "project_path": request["project_path"], "authority_receipt": authority, **authority}


def _validate_integration_authority_receipt(request: dict[str, Any]) -> dict[str, Any]:
    _, ledger_path, _, lock_path, owner_ref = _evolution_paths(
        request, query=True, allow_tree_topic=True
    )
    _expect_keys(request, {"protocol_version", "operation", "project_path", "project_id", "tree_id", "actor_topic_id", "actor_conversation_ref", "implementation_id", "authority_receipt"}, "validate-integration-authority-receipt request")
    with lock_path.open("a+b") as lock_stream:
        _flock_with_timeout(lock_stream)
        frontmatter, records = _load_records(ledger_path)
        _verify_topic_owner(records, request["actor_topic_id"], owner_ref)
        current = _integration_authority(request, frontmatter, records)
        if request["authority_receipt"] != current:
            raise ProtocolError("integration_receipt_stale", "integration authority receipt no longer equals discussion ledger state", retryable=True, context={"state": "stale", "ledger_revision": int(frontmatter["ledger_revision"]), "project_id": request["project_id"], "tree_id": request["tree_id"], "topic_id": request["actor_topic_id"]})
        return {
            "ok": True,
            "state": "valid",
            "project_path": request["project_path"],
            **current,
        }


def _repair_checkpoint(request: dict[str, Any]) -> dict[str, Any]:
    project, ledger_path, _, lock_path, owner_ref, storage_kind = _checkpoint_paths(request)
    if storage_kind != "git":
        raise ProtocolError("checkpoint_identity_conflict", "repair-checkpoint currently applies to Git checkpoint commits")
    _expect_keys(
        request,
        {
            "protocol_version", "operation", "project_path", "project_id", "tree_id",
            "actor_topic_id", "actor_conversation_ref", "expected_ledger_revision",
            "expected_topic_revision", "idempotency_key", "checkpoint_id",
            "expected_checkpoint_revision", "replacement_commit", "active_source_ack",
            "replacement_base_ref",
        },
        "repair-checkpoint request",
    )
    _validate_uuid4(request["idempotency_key"], "idempotency_key")
    replacement = _expect_string(request["replacement_commit"], "replacement_commit", max_bytes=128)
    replacement_base_ref = _expect_string(request["replacement_base_ref"], "replacement_base_ref", max_bytes=512)
    ack = request["active_source_ack"]
    if ack is not None:
        if not isinstance(ack, dict):
            raise ProtocolError("invalid_request", "active_source_ack must be null or an object")
        _expect_keys(ack, {"acknowledged", "checkpoint_id", "broken_identity"}, "active_source_ack")
    with lock_path.open("a+b") as lock_stream:
        _flock_with_timeout(lock_stream)
        frontmatter, records = _load_records(ledger_path)
        replay = _idempotent_result(records, request)
        if replay is not None:
            return replay
        topic_record = _record_by_id(records["Current Topics"], "topic_id", request["actor_topic_id"], "topic_id")
        ledger_revision, topic_revision = _validate_revisions(request, frontmatter, topic_record)
        _verify_topic_owner(records, request["actor_topic_id"], owner_ref)
        record = _checkpoint_record(records, request["checkpoint_id"])
        checkpoint = _checkpoint_data(record)
        if request["expected_checkpoint_revision"] != checkpoint["record_revision"] or checkpoint["state"] != "broken":
            raise ProtocolError("checkpoint_identity_conflict", "checkpoint is not the expected broken fact")
        replacement_parent = _verify_git_base(project, replacement_base_ref)
        match = (
            _commit_matches_checkpoint(
                project,
                replacement,
                checkpoint,
                expected_parent=replacement_parent,
            )
            if replacement_parent is not None
            else None
        )
        matches = (
            _all_checkpoint_candidates(
                project, checkpoint, expected_parent=replacement_parent
            )
            if replacement_parent is not None
            else []
        )
        if match is None or [item["commit_id"] for item in matches] != [replacement]:
            raise ProtocolError("checkpoint_repair_not_unique", "replacement commit is not the unique fully verified checkpoint")
        active_sources = [
            item for item in records["Dependencies and Active Implementations"]
            if item.get("source_checkpoint_id") == checkpoint["checkpoint_id"] and item.get("state") == "active"
        ]
        if active_sources and (
            not isinstance(ack, dict)
            or ack.get("acknowledged") is not True
            or ack.get("checkpoint_id") != checkpoint["checkpoint_id"]
            or ack.get("broken_identity") != checkpoint["broken_identity"]
        ):
            raise ProtocolError("checkpoint_active_source_ack_required", "active implementations require a fresh acknowledgement before checkpoint repair")
        _inject_failure("repair-before-result-record")
        checkpoint["state"] = "completed"
        checkpoint["record_revision"] += 1
        checkpoint["replacement_identity"] = replacement
        checkpoint["repaired_from"] = checkpoint["broken_identity"]
        checkpoint["published_identity"] = replacement
        checkpoint["tree_id"] = match["tree_id"]
        checkpoint["replacement_parent"] = replacement_parent
        checkpoint_ref = checkpoint.get("checkpoint_ref")
        if checkpoint_ref:
            _git(project, ["update-ref", checkpoint_ref, replacement])
        checkpoint["active_source_ack_revision"] = checkpoint["record_revision"] if active_sources else None
        _store_checkpoint(record, checkpoint)
        next_revision = ledger_revision + 1
        result = {
            "ok": True, "state": "completed", "idempotent_replay": False,
            "project_id": request["project_id"], "tree_id": request["tree_id"], "topic_id": request["actor_topic_id"],
            "ledger_revision": next_revision, "record_revision": topic_revision,
            "checkpoint_id": checkpoint["checkpoint_id"], "checkpoint_record_revision": checkpoint["record_revision"],
            "broken_identity": checkpoint["broken_identity"], "replacement_identity": replacement,
            "original_fact_preserved": True, "active_source_acknowledged": bool(active_sources),
        }
        _write_ledger_transaction(ledger_path, frontmatter, records, request, ledger_revision=next_revision, event_type="checkpoint-repaired", result=result)
        return result


def _checkpoint_gc_candidates(ledger_path: Path, records: dict[str, list[dict[str, Any]]]) -> list[dict[str, Any]]:
    snapshot_root = ledger_path.parents[4] / "checkpoints" / "sha256"
    if not snapshot_root.exists():
        return []
    referenced = {
        checkpoint.get("published_identity")
        for checkpoint in (_checkpoint_data(record) for record in records["Checkpoints"])
        if checkpoint.get("state") not in {"cancelled", "superseded"}
    }
    uncertain = {
        checkpoint.get("published_identity")
        for checkpoint in (_checkpoint_data(record) for record in records["Checkpoints"])
        if checkpoint.get("state") in {"outcome-unknown", "broken"}
    }
    uncertain_objects: set[str] = set()
    for checkpoint in (
        _checkpoint_data(record) for record in records["Checkpoints"]
    ):
        if (
            checkpoint.get("storage_kind") != "non-git"
            or checkpoint.get("state") not in CHECKPOINT_ACTIVE_STATES
        ):
            continue
        uncertain_objects.add(checkpoint["snapshot_digest"])
    candidates = []
    for path in sorted(snapshot_root.glob("*/*")):
        if not path.is_file():
            continue
        digest = path.name
        if digest in referenced or digest in uncertain or digest in uncertain_objects:
            continue
        candidates.append({"digest": digest, "path": str(path)})
    return candidates


def _canonical_checkpoint_snapshot_path(ledger_path: Path, digest: str) -> Path:
    if not isinstance(digest, str) or not re.fullmatch(r"[0-9a-f]{64}", digest):
        raise ProtocolError("checkpoint_snapshot_corrupt", "GC candidate digest is invalid")
    root = (ledger_path.parents[4] / "checkpoints" / "sha256").resolve()
    path = root / digest[:2] / digest
    if path.parent != root / digest[:2] or not path.is_absolute():
        raise ProtocolError("checkpoint_snapshot_corrupt", "GC candidate path is invalid")
    return path


def _validate_checkpoint_gc_candidate_path(
    ledger_path: Path, item: dict[str, Any]
) -> Path:
    if set(item) != {"digest", "path"}:
        raise ProtocolError("checkpoint_snapshot_corrupt", "GC candidate shape is invalid")
    expected = _canonical_checkpoint_snapshot_path(ledger_path, item["digest"])
    supplied = Path(_expect_string(item["path"], "GC candidate path", max_bytes=4096))
    if supplied != expected:
        raise ProtocolError(
            "checkpoint_snapshot_corrupt",
            "GC candidate path is outside the canonical snapshot root",
        )
    return expected


def _checkpoint_gc_dry_run(request: dict[str, Any]) -> dict[str, Any]:
    project, ledger_path, _, lock_path, owner_ref = _evolution_paths(request, query=True)
    _, storage_kind = _coordination_root(project)
    if storage_kind != "non-git":
        raise ProtocolError("checkpoint_identity_conflict", "snapshot GC requires a non-Git project")
    _expect_keys(request, {"protocol_version", "operation", "project_path", "project_id", "tree_id", "actor_topic_id", "actor_conversation_ref"}, "checkpoint-gc-dry-run request")
    with lock_path.open("a+b") as lock_stream:
        _flock_with_timeout(lock_stream)
        frontmatter, records = _load_records(ledger_path)
        _verify_topic_owner(records, request["actor_topic_id"], owner_ref)
        active_gc = _active_checkpoint_gc(records)
        if active_gc is not None:
            raise ProtocolError(
                "checkpoint_gc_reconciliation_required",
                "checkpoint GC must be reconciled before another dry-run",
                context={"gc_operation_id": active_gc["result_id"]},
            )
        candidates = _checkpoint_gc_candidates(ledger_path, records)
        candidate_digest = _sha256(_canonical_json(candidates).encode("utf-8"))
        return {
            "ok": True, "state": "dry-run", "project_id": request["project_id"], "tree_id": request["tree_id"],
            "topic_id": request["actor_topic_id"], "ledger_revision": int(frontmatter["ledger_revision"]),
            "record_revision": _record_by_id(records["Current Topics"], "topic_id", request["actor_topic_id"], "topic_id")["record_revision"],
            "candidates": candidates, "candidate_digest": candidate_digest, "confirmation_required": True,
        }


def _checkpoint_gc_confirm(request: dict[str, Any]) -> dict[str, Any]:
    project, ledger_path, _, lock_path, owner_ref, storage_kind = _checkpoint_paths(request)
    if storage_kind != "non-git":
        raise ProtocolError("checkpoint_identity_conflict", "snapshot GC requires a non-Git project")
    _expect_keys(
        request,
        {
            "protocol_version", "operation", "project_path", "project_id", "tree_id",
            "actor_topic_id", "actor_conversation_ref", "expected_ledger_revision",
            "expected_topic_revision", "idempotency_key", "candidate_digest", "candidates",
        },
        "checkpoint-gc-confirm request",
    )
    _validate_uuid4(request["idempotency_key"], "idempotency_key")
    supplied_candidates = request["candidates"]
    if not isinstance(supplied_candidates, list) or any(not isinstance(item, dict) or set(item) != {"digest", "path"} for item in supplied_candidates):
        raise ProtocolError("invalid_request", "candidates must be the exact dry-run candidate objects")
    with lock_path.open("a+b") as lock_stream:
        _flock_with_timeout(lock_stream)
        frontmatter, records = _load_records(ledger_path)
        replay = _idempotent_result(records, request)
        if replay is not None:
            return replay
        topic_record = _record_by_id(records["Current Topics"], "topic_id", request["actor_topic_id"], "topic_id")
        ledger_revision, topic_revision = _validate_revisions(request, frontmatter, topic_record)
        _verify_topic_owner(records, request["actor_topic_id"], owner_ref)
        active_gc = _active_checkpoint_gc(records)
        if active_gc is not None:
            raise ProtocolError(
                "checkpoint_gc_reconciliation_required",
                "checkpoint GC must be reconciled before another confirmation",
                context={"gc_operation_id": active_gc["result_id"]},
            )
        actual = _checkpoint_gc_candidates(ledger_path, records)
        actual_digest = _sha256(_canonical_json(actual).encode("utf-8"))
        if actual != supplied_candidates or request["candidate_digest"] != actual_digest:
            raise ProtocolError("checkpoint_gc_confirmation_mismatch", "GC confirmation does not bind the current exact candidates")
        deleted = []
        for item in actual:
            path = _validate_checkpoint_gc_candidate_path(ledger_path, item)
            content = _require_regular_nosymlink(path, "checkpoint snapshot GC candidate")
            if _sha256(content) != item["digest"]:
                raise ProtocolError("checkpoint_snapshot_corrupt", "GC candidate digest does not match its object name")
        _inject_failure("gc-before-delete")
        gc_operation_id = _checkpoint_gc_id(request["idempotency_key"])
        gc_operation = {
            "gc_operation_id": gc_operation_id,
            "record_revision": 1,
            "state": "outcome-unknown",
            "candidate_digest": actual_digest,
            "candidates_json": _canonical_json(actual),
            "confirmation_ledger_revision": ledger_revision,
        }
        gc_record = {
            "result_id": gc_operation_id,
            "result_kind": "checkpoint-gc",
            "state": "outcome-unknown",
            "record_revision": 1,
            "data_json": _canonical_json(gc_operation),
        }
        records["Phase Results"].append(gc_record)
        outcome_revision = ledger_revision + 1
        outcome_result = {
            "ok": True,
            "state": "outcome-unknown",
            "idempotent_replay": False,
            "project_id": request["project_id"],
            "tree_id": request["tree_id"],
            "topic_id": request["actor_topic_id"],
            "ledger_revision": outcome_revision,
            "record_revision": topic_revision,
            "gc_operation_id": gc_operation_id,
            "gc_record_revision": 1,
            "candidate_digest": actual_digest,
            "candidates": actual,
            "reconcile_required": True,
        }
        _write_ledger_transaction(
            ledger_path,
            frontmatter,
            records,
            request,
            ledger_revision=outcome_revision,
            event_type="checkpoint-gc-outcome-unknown",
            result=outcome_result,
        )
        _inject_failure("gc-after-outcome-unknown-record")
        for item in actual:
            _validate_checkpoint_gc_candidate_path(ledger_path, item).unlink()
            deleted.append(item)
            _inject_failure("gc-during-delete")
        _inject_failure("gc-after-delete-before-result-record")
        gc_operation["state"] = "completed"
        gc_operation["record_revision"] += 1
        gc_operation["deleted_json"] = _canonical_json(deleted)
        _store_checkpoint_gc(gc_record, gc_operation)
        next_revision = outcome_revision + 1
        result = {
            "ok": True, "state": "completed", "idempotent_replay": False,
            "project_id": request["project_id"], "tree_id": request["tree_id"], "topic_id": request["actor_topic_id"],
            "ledger_revision": next_revision, "record_revision": topic_revision,
            "gc_operation_id": gc_operation_id,
            "gc_record_revision": gc_operation["record_revision"],
            "deleted": deleted, "candidate_digest": actual_digest,
        }
        _write_ledger_transaction(ledger_path, frontmatter, records, request, ledger_revision=next_revision, event_type="checkpoint-gc-completed", result=result)
        return result


def _reconcile_checkpoint_gc(request: dict[str, Any]) -> dict[str, Any]:
    project, ledger_path, _, lock_path, owner_ref, storage_kind = _checkpoint_paths(request)
    if storage_kind != "non-git":
        raise ProtocolError("checkpoint_identity_conflict", "snapshot GC requires a non-Git project")
    _expect_keys(
        request,
        {
            "protocol_version", "operation", "project_path", "project_id", "tree_id",
            "actor_topic_id", "actor_conversation_ref", "expected_ledger_revision",
            "expected_topic_revision", "idempotency_key", "gc_operation_id",
            "expected_gc_revision",
        },
        "reconcile-checkpoint-gc request",
    )
    _validate_uuid4(request["idempotency_key"], "idempotency_key")
    with lock_path.open("a+b") as lock_stream:
        _flock_with_timeout(lock_stream)
        frontmatter, records = _load_records(ledger_path)
        replay = _idempotent_result(records, request)
        if replay is not None:
            return replay
        topic_record = _record_by_id(records["Current Topics"], "topic_id", request["actor_topic_id"], "topic_id")
        ledger_revision, topic_revision = _validate_revisions(request, frontmatter, topic_record)
        _verify_topic_owner(records, request["actor_topic_id"], owner_ref)
        record = _checkpoint_gc_record(
            records, _expect_string(request["gc_operation_id"], "gc_operation_id")
        )
        operation = _checkpoint_gc_data(record)
        if (
            request["expected_gc_revision"] != operation["record_revision"]
            or operation["state"] != "outcome-unknown"
        ):
            raise ProtocolError(
                "checkpoint_gc_confirmation_mismatch",
                "checkpoint GC is not the expected outcome-unknown operation",
            )
        candidates = json.loads(operation["candidates_json"])
        if operation["candidate_digest"] != _sha256(
            _canonical_json(candidates).encode("utf-8")
        ):
            raise ProtocolError("state_corrupt", "checkpoint GC frozen candidates are invalid")
        already_deleted = []
        deleted = []
        for item in candidates:
            path = _validate_checkpoint_gc_candidate_path(ledger_path, item)
            if not path.exists():
                already_deleted.append(item)
                continue
            content = _require_regular_nosymlink(path, "checkpoint snapshot GC candidate")
            if _sha256(content) != item["digest"]:
                raise ProtocolError(
                    "checkpoint_snapshot_corrupt",
                    "remaining GC candidate digest does not match its object name",
                )
            path.unlink()
            deleted.append(item)
        operation["state"] = "completed"
        operation["record_revision"] += 1
        operation["deleted_json"] = _canonical_json(candidates)
        _store_checkpoint_gc(record, operation)
        next_revision = ledger_revision + 1
        result = {
            "ok": True,
            "state": "completed",
            "idempotent_replay": False,
            "project_id": request["project_id"],
            "tree_id": request["tree_id"],
            "topic_id": request["actor_topic_id"],
            "ledger_revision": next_revision,
            "record_revision": topic_revision,
            "gc_operation_id": operation["gc_operation_id"],
            "gc_record_revision": operation["record_revision"],
            "candidate_digest": operation["candidate_digest"],
            "deleted": candidates,
            "deleted_during_reconciliation": deleted,
            "already_deleted": already_deleted,
            "recovered_outcome_unknown": True,
        }
        _write_ledger_transaction(
            ledger_path,
            frontmatter,
            records,
            request,
            ledger_revision=next_revision,
            event_type="checkpoint-gc-reconciled",
            result=result,
        )
        return result


def _handoff_id(idempotency_key: str) -> str:
    return f"H-{uuid.UUID(idempotency_key).hex}"


def _handoff_attempt_id(handoff_id: str, number: int) -> str:
    return f"A-{handoff_id[2:]}-{number}"


def _handoff_record(
    records: dict[str, list[dict[str, Any]]], handoff_id: Any
) -> dict[str, Any]:
    return _record_by_id(
        records["Phase Runs"],
        "run_id",
        _expect_string(handoff_id, "handoff_id", max_bytes=64),
        "handoff_id",
    )


def _handoff_data(record: dict[str, Any]) -> dict[str, Any]:
    data = _json_field(record, "data_json", "handoff")
    if (
        not isinstance(data, dict)
        or record.get("run_kind") != "discussion-handoff"
        or record.get("run_id") != data.get("handoff_id")
        or record.get("state") != data.get("state")
        or record.get("record_revision") != data.get("record_revision")
    ):
        raise ProtocolError("state_corrupt", "handoff record envelope is invalid")
    return data


def _store_handoff(record: dict[str, Any], handoff: dict[str, Any]) -> None:
    record["state"] = handoff["state"]
    record["record_revision"] = handoff["record_revision"]
    record["data_json"] = _canonical_json(handoff)


def _handoff_attempt(handoff: dict[str, Any], attempt_id: Any) -> dict[str, Any]:
    value = _expect_string(attempt_id, "attempt_id", max_bytes=80)
    matches = [attempt for attempt in handoff["attempts"] if attempt.get("attempt_id") == value]
    if len(matches) != 1:
        raise ProtocolError("record_not_found", "attempt_id does not identify one handoff attempt")
    return matches[0]


def _validated_string_list(
    value: Any, label: str, *, allow_empty: bool = False, max_items: int = 64
) -> list[str]:
    if not isinstance(value, list) or (not allow_empty and not value) or len(value) > max_items:
        raise ProtocolError("invalid_request", f"{label} must be a bounded array")
    result = [_expect_string(item, f"{label} item", max_bytes=512) for item in value]
    if len(set(result)) != len(result):
        raise ProtocolError("invalid_request", f"{label} values must be unique")
    return result


def _validate_work_snapshot(value: Any) -> tuple[dict[str, Any], int]:
    if not isinstance(value, dict) or "goal" not in value or not set(value).issubset(
        HANDOFF_WORK_SNAPSHOT_FIELDS
    ):
        raise ProtocolError("invalid_request", "work_snapshot fields are not allowlisted")
    snapshot: dict[str, Any] = {}
    for key, item in value.items():
        if key == "goal":
            snapshot[key] = _expect_string(item, "work_snapshot.goal", max_bytes=24000)
        else:
            snapshot[key] = _validated_string_list(
                item, f"work_snapshot.{key}", allow_empty=True
            )
    size = len(_canonical_json(snapshot).encode("utf-8"))
    if size > HANDOFF_WORK_SNAPSHOT_MAX_BYTES:
        raise ProtocolError("handoff_payload_too_large", "work snapshot exceeds its size limit")
    return snapshot, size


def _validate_authoritative_references(value: Any) -> tuple[list[dict[str, str]], str]:
    if not isinstance(value, list) or len(value) > 64:
        raise ProtocolError("invalid_request", "authoritative_references must be a bounded array")
    result = []
    for index, item in enumerate(value):
        if not isinstance(item, dict):
            raise ProtocolError("invalid_request", f"authoritative_references[{index}] must be an object")
        _expect_keys(item, {"kind", "identity", "sha256"}, "authoritative reference")
        digest = _expect_string(item["sha256"], "authoritative reference sha256", max_bytes=64)
        if not SHA256_RE.fullmatch(digest):
            raise ProtocolError("invalid_request", "authoritative reference sha256 is invalid")
        result.append(
            {
                "kind": _expect_string(item["kind"], "authoritative reference kind", max_bytes=64),
                "identity": _expect_string(item["identity"], "authoritative reference identity", max_bytes=1024),
                "sha256": digest,
            }
        )
    return result, _sha256(_canonical_json(result).encode("utf-8"))


def _handoff_payload(
    handoff: dict[str, Any], attempt_id: str
) -> tuple[dict[str, Any], str, int]:
    envelope = {
        "project_id": handoff["project_id"],
        "tree_id": handoff["tree_id"],
        "topic_id": handoff["target_topic_id"],
        "parent_topic_id": handoff["source_topic_id"] if handoff["kind"] == "child" else None,
        "handoff_id": handoff["handoff_id"],
        "attempt_id": attempt_id,
    }
    payload = {
        "identity_envelope": envelope,
        "work_snapshot": handoff["work_snapshot"],
        "authoritative_references": handoff["authoritative_references"],
    }
    payload_bytes = len(_canonical_json(payload).encode("utf-8"))
    if payload_bytes > HANDOFF_PAYLOAD_MAX_BYTES:
        raise ProtocolError("handoff_payload_too_large", "handoff payload exceeds its size limit")
    return envelope, _sha256(_canonical_json(payload).encode("utf-8")), payload_bytes


def _handoff_result(
    request: dict[str, Any],
    handoff: dict[str, Any],
    attempt: dict[str, Any],
    *,
    ledger_revision: int,
    topic_revision: int,
    state: str | None = None,
) -> dict[str, Any]:
    return {
        "ok": True,
        "state": state or handoff["state"],
        "idempotent_replay": False,
        "project_id": request["project_id"],
        "tree_id": request["tree_id"],
        "topic_id": request["actor_topic_id"],
        "target_topic_id": handoff["target_topic_id"],
        "ledger_revision": ledger_revision,
        "record_revision": topic_revision,
        "handoff_id": handoff["handoff_id"],
        "attempt_id": attempt["attempt_id"],
        "payload_sha256": attempt["payload_sha256"],
        "authoritative_references_sha256": handoff["authoritative_references_sha256"],
    }


def _phase_paths(request: dict[str, Any], *, query: bool = False) -> tuple[Path, Path, Path, Path, str]:
    return _evolution_paths(request, query=query, allow_tree_topic=True)


def _phase_record(records: dict[str, list[dict[str, Any]]], run_id: str) -> dict[str, Any]:
    record = _record_by_id(records["Phase Runs"], "run_id", run_id, "phase_run_id")
    if record.get("run_kind") != "phase-run":
        raise ProtocolError("phase_identity_conflict", "record is not a lifecycle Phase Run")
    data = _json_field(record, "data_json", "phase run")
    if data.get("run_id") != run_id or record.get("state") != data.get("state"):
        raise ProtocolError("state_corrupt", "Phase Run envelope does not match its data")
    return record


def _phase_data(record: dict[str, Any]) -> dict[str, Any]:
    data = _json_field(record, "data_json", "phase run")
    if data.get("state") not in PHASE_RUN_STATES:
        raise ProtocolError("state_corrupt", "Phase Run has an unsupported state")
    evidence = data.get("evidence")
    if (
        not isinstance(evidence, dict)
        or set(evidence) != set(PHASE_DRIFT_CODES)
        or any(not isinstance(value, str) or not SHA256_RE.fullmatch(value) for value in evidence.values())
    ):
        raise ProtocolError("state_corrupt", "Phase Run frozen evidence is incomplete or invalid")
    attempts = data.get("attempts")
    if not isinstance(attempts, list) or not attempts:
        raise ProtocolError("state_corrupt", "Phase Run has no external attempt history")
    for index, attempt in enumerate(attempts, start=1):
        if attempt.get("attempt_number") != index or attempt.get("attempt_id") != f"PA-{data['run_id'][3:]}-{index}":
            raise ProtocolError("state_corrupt", "Phase Run attempt identities are not monotonic")
        if attempt.get("state") not in PHASE_ATTEMPT_STATES:
            raise ProtocolError("state_corrupt", "Phase Run attempt has an unsupported state")
    return data


def _verify_phase_source(data: dict[str, Any], actor_topic_id: str) -> None:
    if data.get("source_topic_id") != actor_topic_id:
        raise ProtocolError(
            "phase_identity_conflict", "Phase Run belongs to another source topic"
        )


def _phase_attempt(data: dict[str, Any], attempt_id: str) -> dict[str, Any]:
    matches = [item for item in data["attempts"] if item.get("attempt_id") == attempt_id]
    if len(matches) != 1:
        raise ProtocolError("record_not_found", "phase_attempt_id does not identify one attempt")
    return matches[0]


def _store_phase(record: dict[str, Any], data: dict[str, Any]) -> None:
    record["state"] = data["state"]
    record["record_revision"] = data["record_revision"]
    record["data_json"] = _canonical_json(data)


def _phase_request_context(request: dict[str, Any], allowed: set[str], *, query: bool = False) -> tuple[Path, Path, Path, str]:
    required = {
        "protocol_version", "operation", "project_path", "project_id", "tree_id",
        "actor_topic_id", "actor_conversation_ref",
    } | allowed
    if not query:
        required |= {"expected_ledger_revision", "expected_topic_revision", "idempotency_key"}
    _expect_keys(request, required, f"{request['operation']} request")
    if not query:
        _validate_uuid4(request["idempotency_key"], "idempotency_key")
    _, ledger_path, topic_path, lock_path, _ = _phase_paths(request, query=query)
    return ledger_path, topic_path, lock_path, _expect_string(request["actor_conversation_ref"], "actor_conversation_ref")


def _phase_evidence(request: dict[str, Any], *, required: bool = False) -> dict[str, str]:
    value = request.get("evidence")
    if value is None and not required:
        return {}
    if not isinstance(value, dict):
        raise ProtocolError("invalid_request", "evidence must be an object")
    allowed = {"source", "route", "impact", "coverage", "dependency", "coordination"}
    if any(key not in allowed for key in value):
        raise ProtocolError("invalid_request", "evidence contains an unsupported dimension")
    result: dict[str, str] = {}
    for key, item in value.items():
        if not isinstance(item, str) or not SHA256_RE.fullmatch(item):
            raise ProtocolError("invalid_request", f"evidence.{key} must be a SHA-256 digest")
        result[key] = item
    if required and set(result) != allowed:
        raise ProtocolError("invalid_request", "evidence must include all frozen dimensions")
    return result


def _phase_check_evidence(data: dict[str, Any], supplied: dict[str, str]) -> None:
    frozen = data.get("evidence", {})
    for dimension, expected in frozen.items():
        if supplied.get(dimension) != expected:
            raise ProtocolError(
                PHASE_DRIFT_CODES[dimension],
                f"frozen {dimension} evidence no longer matches the source topic",
            )


def _completed_checkpoint(
    records: dict[str, list[dict[str, Any]]], checkpoint_id: Any
) -> dict[str, Any]:
    record = _checkpoint_record(
        records, _expect_string(checkpoint_id, "source_checkpoint_id", max_bytes=64)
    )
    checkpoint = _checkpoint_data(record)
    if (
        checkpoint.get("state") != "completed"
        or checkpoint.get("purpose") != "stage-entry"
        or not checkpoint.get("published_identity")
    ):
        raise ProtocolError(
            "phase_checkpoint_invalid",
            "wrapper phase source must be a completed stage checkpoint",
        )
    return checkpoint


def _pending_topic_impacts(
    records: dict[str, list[dict[str, Any]]], topic_id: str
) -> list[dict[str, Any]]:
    return [
        item
        for item in _topic_snapshot(records, topic_id)["impacts"]
        if item.get("state") == "pending"
    ]


def _verify_wrapper_checkpoint_current(
    records: dict[str, list[dict[str, Any]]],
    data: dict[str, Any],
    topic_path: Path,
) -> None:
    checkpoint = _completed_checkpoint(records, data["source_checkpoint_id"])
    if (
        checkpoint.get("topic_id") != data.get("source_topic_id")
        or checkpoint.get("published_identity") != data.get("source_checkpoint_identity")
    ):
        raise ProtocolError("phase_checkpoint_invalid", "wrapper checkpoint identity has changed")
    completed_sources = [
        _checkpoint_data(item)
        for item in records["Checkpoints"]
        if item.get("topic_id") == data.get("source_topic_id")
        and item.get("state") == "completed"
        and _checkpoint_data(item).get("purpose") == "stage-entry"
    ]
    if not completed_sources or completed_sources[-1]["checkpoint_id"] != checkpoint["checkpoint_id"]:
        raise ProtocolError("phase_checkpoint_invalid", "wrapper checkpoint is no longer the latest source")
    document_digests = json.loads(checkpoint["document_digests_json"])
    if _sha256(_require_regular_nosymlink(topic_path, "topic document")) not in document_digests.values():
        raise ProtocolError("phase_source_drift", "topic document differs from the frozen checkpoint")


def _verify_stage_one_checkpoint(
    records: dict[str, list[dict[str, Any]]], checkpoint: dict[str, Any]
) -> str:
    result_id = checkpoint.get("stage_entry_phase_result_id")
    if checkpoint.get("stage_entry_phase") != 1 or not isinstance(result_id, str):
        raise ProtocolError(
            "phase_flow_mode_invalid",
            "continuous mode requires a checkpoint from a completed stage 1",
        )
    result_record = _record_by_id(
        records["Phase Results"], "result_id", result_id, "phase_result_id"
    )
    result = _json_field(result_record, "data_json", "phase result")
    if (
        result_record.get("result_kind") != "phase-result"
        or result_record.get("state") != "completed"
        or result.get("from_phase") != 0
        or result.get("to_phase") != 1
    ):
        raise ProtocolError(
            "phase_flow_mode_invalid",
            "continuous mode stage-1 result is invalid",
        )
    phase_run_record = _phase_record(records, result.get("phase_run_id"))
    phase_run = _phase_data(phase_run_record)
    if (
        phase_run.get("state") != "completed"
        or phase_run.get("source_topic_id") != checkpoint.get("topic_id")
        or phase_run.get("from_phase") != 0
        or phase_run.get("to_phase") != 1
    ):
        raise ProtocolError(
            "phase_flow_mode_invalid",
            "continuous mode stage-1 result does not match the source topic",
        )
    return result_id


def _verify_continuous_flow_authority(
    records: dict[str, list[dict[str, Any]]],
    checkpoint: dict[str, Any],
    authorization_id: str | None = None,
) -> dict[str, Any]:
    result_id = _verify_stage_one_checkpoint(records, checkpoint)
    matches = []
    for record in records["Phase Results"]:
        if (
            record.get("result_kind") != "continuous-flow-authorization"
            or record.get("state") != "authorized"
            or (authorization_id is not None and record.get("result_id") != authorization_id)
        ):
            continue
        authority = _json_field(record, "data_json", "continuous flow authorization")
        if (
            authority.get("topic_id") == checkpoint.get("topic_id")
            and authority.get("phase_result_id") == result_id
            and authority.get("source_checkpoint_id") == checkpoint.get("checkpoint_id")
            and authority.get("source_checkpoint_identity")
            == checkpoint.get("published_identity")
            and authority.get("user_reply") == "执行后续全部流程"
        ):
            matches.append(authority)
    if len(matches) != 1:
        raise ProtocolError(
            "phase_flow_mode_invalid",
            "continuous mode requires one exact successful-footer authorization",
        )
    return matches[0]


def _authorize_continuous_flow(request: dict[str, Any]) -> dict[str, Any]:
    ledger_path, topic_path, lock_path, owner_ref = _phase_request_context(
        request,
        {
            "source_checkpoint_id",
            "source_checkpoint_identity",
            "phase_result_id",
            "user_reply",
        },
    )[:4]
    user_reply = _expect_string(request["user_reply"], "user_reply", max_bytes=128)
    source_checkpoint_identity = _expect_string(
        request["source_checkpoint_identity"],
        "source_checkpoint_identity",
        max_bytes=128,
    )
    requested_phase_result_id = _expect_string(
        request["phase_result_id"], "phase_result_id", max_bytes=64
    )
    if user_reply != "执行后续全部流程":
        raise ProtocolError(
            "phase_flow_mode_invalid",
            "continuous authorization requires the exact successful-footer reply",
        )
    with lock_path.open("a+b") as lock_stream:
        _flock_with_timeout(lock_stream)
        frontmatter, records = _load_records(ledger_path)
        replay = _idempotent_result(records, request)
        if replay is not None:
            return replay
        topic = _record_by_id(
            records["Current Topics"], "topic_id", request["actor_topic_id"], "topic_id"
        )
        ledger_revision, topic_revision = _validate_revisions(request, frontmatter, topic)
        _verify_topic_owner(records, request["actor_topic_id"], owner_ref)
        if topic.get("current_phase") != 1:
            raise ProtocolError(
                "phase_flow_mode_invalid",
                "continuous authorization is available only after stage 1",
            )
        checkpoint = _completed_checkpoint(records, request["source_checkpoint_id"])
        if (
            checkpoint.get("topic_id") != request["actor_topic_id"]
            or source_checkpoint_identity != checkpoint.get("published_identity")
        ):
            raise ProtocolError(
                "phase_checkpoint_invalid",
                "continuous authorization checkpoint proof does not match the source topic",
            )
        _verify_wrapper_checkpoint_current(
            records,
            {
                "source_topic_id": request["actor_topic_id"],
                "source_checkpoint_id": checkpoint["checkpoint_id"],
                "source_checkpoint_identity": checkpoint["published_identity"],
            },
            topic_path,
        )
        phase_result_id = _verify_stage_one_checkpoint(records, checkpoint)
        if requested_phase_result_id != phase_result_id:
            raise ProtocolError(
                "phase_flow_mode_invalid",
                "continuous authorization phase result does not match the checkpoint",
            )
        existing = [
            item
            for item in records["Phase Results"]
            if item.get("result_kind") == "continuous-flow-authorization"
            and item.get("state") == "authorized"
            and _json_field(item, "data_json", "continuous flow authorization").get(
                "source_checkpoint_id"
            )
            == checkpoint["checkpoint_id"]
        ]
        if existing:
            raise ProtocolError(
                "phase_flow_mode_invalid",
                "continuous flow is already authorized for this checkpoint",
            )
        authorization_id = f"CF-{uuid.UUID(request['idempotency_key']).hex}"
        authority = {
            "authorization_id": authorization_id,
            "topic_id": request["actor_topic_id"],
            "phase_result_id": phase_result_id,
            "source_checkpoint_id": checkpoint["checkpoint_id"],
            "source_checkpoint_identity": checkpoint["published_identity"],
            "user_reply": user_reply,
        }
        records["Phase Results"].append(
            {
                "result_id": authorization_id,
                "result_kind": "continuous-flow-authorization",
                "state": "authorized",
                "record_revision": 1,
                "data_json": _canonical_json(authority),
            }
        )
        next_revision = ledger_revision + 1
        result = {
            "ok": True,
            "state": "authorized",
            "idempotent_replay": False,
            "project_id": request["project_id"],
            "tree_id": request["tree_id"],
            "topic_id": request["actor_topic_id"],
            "ledger_revision": next_revision,
            "record_revision": topic_revision,
            "continuous_authorization_id": authorization_id,
            "phase_result_id": phase_result_id,
            "source_checkpoint_id": checkpoint["checkpoint_id"],
            "source_checkpoint_identity": checkpoint["published_identity"],
        }
        _write_ledger_transaction(
            ledger_path,
            frontmatter,
            records,
            request,
            ledger_revision=next_revision,
            event_type="continuous-flow-authorized",
            result=result,
        )
        return result


def _prepare_wrapper_phase_run(request: dict[str, Any]) -> dict[str, Any]:
    ledger_path, topic_path, lock_path, owner_ref = _phase_request_context(
        request,
        {
            "from_phase", "to_phase", "route", "carrier_kind",
            "source_checkpoint_id", "flow_mode", "flow_mode_source",
            "requirement_completeness", "scope",
        },
    )[:4]
    from_phase = request["from_phase"]
    to_phase = request["to_phase"]
    if not isinstance(from_phase, int) or not isinstance(to_phase, int):
        raise ProtocolError("invalid_request", "phase values must be integers")
    route = (from_phase, to_phase)
    if route not in PHASE_ROUTES:
        raise ProtocolError("invalid_phase_route", "only the six approved forward routes are legal")
    supplied_route = _expect_string(request["route"], "route", max_bytes=32)
    if supplied_route not in {f"{from_phase}->{to_phase}", f"{from_phase}\u2192{to_phase}"}:
        raise ProtocolError("phase_route_conflict", "route label does not match the requested phase transition")
    carrier_kind = _expect_string(request["carrier_kind"], "carrier_kind", max_bytes=64)
    if route not in WRAPPER_CARRIER_ROUTES.get(carrier_kind, set()):
        raise ProtocolError("phase_route_conflict", "wrapper carrier is not approved for this route")
    flow_mode = _expect_string(request["flow_mode"], "flow_mode", max_bytes=32)
    flow_source = _expect_string(request["flow_mode_source"], "flow_mode_source", max_bytes=64)
    if flow_mode not in {"stepwise", "continuous"}:
        raise ProtocolError("phase_flow_mode_invalid", "flow_mode is unsupported")
    if flow_mode == "continuous" and (
        from_phase != 1 or flow_source != "successful-stage-1-footer"
    ):
        raise ProtocolError(
            "phase_flow_mode_invalid",
            "continuous mode requires a successful stage-1 footer",
        )
    if flow_mode == "stepwise" and flow_source not in {
        "explicit-stage-confirmation", "successful-stage-1-footer"
    }:
        raise ProtocolError("phase_flow_mode_invalid", "stepwise flow source is unsupported")
    scope = _validated_string_list(request["scope"], "scope")
    completeness = request["requirement_completeness"]
    if route == (1, 3):
        if (
            not isinstance(completeness, dict)
            or set(completeness) != DIRECT_IMPLEMENTATION_COMPLETENESS
            or any(value is not True for value in completeness.values())
        ):
            raise ProtocolError(
                "phase_requirement_incomplete",
                "direct implementation requires complete scope, behavior, failures, "
                "acceptance conditions and test seam",
            )
    elif completeness is not None:
        raise ProtocolError("invalid_request", "requirement_completeness is only valid for route 1->3")
    with lock_path.open("a+b") as lock_stream:
        _flock_with_timeout(lock_stream)
        frontmatter, records = _load_records(ledger_path)
        replay = _idempotent_result(records, request)
        if replay is not None:
            return replay
        topic = _record_by_id(
            records["Current Topics"], "topic_id", request["actor_topic_id"], "topic_id"
        )
        ledger_revision, topic_revision = _validate_revisions(request, frontmatter, topic)
        _verify_topic_owner(records, request["actor_topic_id"], owner_ref)
        if topic.get("current_phase") != from_phase:
            raise ProtocolError("phase_route_conflict", "route source phase does not match current topic phase")
        if _pending_topic_impacts(records, request["actor_topic_id"]):
            raise ProtocolError("phase_impact_drift", "pending impacts must be resolved before stage routing")
        checkpoint = _completed_checkpoint(records, request["source_checkpoint_id"])
        if checkpoint.get("topic_id") != request["actor_topic_id"]:
            raise ProtocolError("phase_checkpoint_invalid", "checkpoint belongs to another topic")
        checkpoint_data = {
            "source_topic_id": request["actor_topic_id"],
            "source_checkpoint_id": checkpoint["checkpoint_id"],
            "source_checkpoint_identity": checkpoint["published_identity"],
        }
        _verify_wrapper_checkpoint_current(records, checkpoint_data, topic_path)
        continuous_authority = None
        if flow_mode == "continuous":
            continuous_authority = _verify_continuous_flow_authority(records, checkpoint)
        active_runs = [
            item for item in records["Phase Runs"]
            if item.get("run_kind") == "phase-run"
            and item.get("state") in {
                "prepared", "setup-pending", "ready", "active",
                "completion-claimed", "completion-pending", "outcome-unknown",
            }
            and _json_field(item, "data_json", "phase run").get("source_topic_id")
            == request["actor_topic_id"]
        ]
        if active_runs:
            raise ProtocolError("phase_coordination_drift", "an active Phase Run already owns this source topic")
        evidence = _authoritative_phase_evidence(topic_path, records, topic)
        run_id = f"PR-{ledger_revision + 1:08d}"
        attempt_id = f"PA-{run_id[3:]}-1"
        stage_ownership = (
            ["spec", "adr", "tickets", "planning-commit"]
            if carrier_kind == "solution-designer"
            else ["shared-0-1-topic"]
            if to_phase == 1
            else ["implementation"]
        )
        data = {
            "run_id": run_id,
            "run_kind": "phase-run",
            "record_revision": 1,
            "state": "prepared",
            "from_phase": from_phase,
            "to_phase": to_phase,
            "route": list(route),
            "carrier_kind": carrier_kind,
            "source_topic_id": request["actor_topic_id"],
            "evidence": evidence,
            "attempts": [{
                "attempt_id": attempt_id,
                "attempt_number": 1,
                "state": "setup-pending",
                "authorization": False,
                "carrier_ref": None,
                "reason": None,
                "claimed": False,
            }],
            "creation_idempotency_key": request["idempotency_key"],
            "wrapper_integration": True,
            "source_checkpoint_id": checkpoint["checkpoint_id"],
            "source_checkpoint_identity": checkpoint["published_identity"],
            "flow_mode": flow_mode,
            "flow_mode_source": flow_source,
            "continuous_authorization_id": (
                continuous_authority["authorization_id"]
                if continuous_authority is not None
                else None
            ),
            "scope": scope,
            "requirement_completeness": completeness,
            "requirement_document_mode": "shared-0-1-topic" if to_phase == 1 else "frozen-read-only",
            "stage_ownership": stage_ownership,
            "may_modify_requirement_source": to_phase == 1,
        }
        record = {
            "run_id": run_id,
            "run_kind": "phase-run",
            "state": "prepared",
            "record_revision": 1,
            "data_json": _canonical_json(data),
        }
        records["Phase Runs"].append(record)
        next_revision = ledger_revision + 1
        result = {
            "ok": True,
            "state": "prepared",
            "idempotent_replay": False,
            "project_id": request["project_id"],
            "tree_id": request["tree_id"],
            "topic_id": request["actor_topic_id"],
            "ledger_revision": next_revision,
            "record_revision": topic_revision,
            "phase_run_id": run_id,
            "attempt_id": attempt_id,
            "route": list(route),
            "evidence": evidence,
            "source_checkpoint_id": checkpoint["checkpoint_id"],
            "source_checkpoint_identity": checkpoint["published_identity"],
            "topic_document_path": str(topic_path),
            "requirement_document_mode": data["requirement_document_mode"],
            "stage_ownership": stage_ownership,
            "may_modify_requirement_source": data["may_modify_requirement_source"],
            "flow_mode": flow_mode,
            "continuous_authorization_id": data["continuous_authorization_id"],
        }
        _write_ledger_transaction(
            ledger_path,
            frontmatter,
            records,
            request,
            ledger_revision=next_revision,
            event_type="wrapper-phase-run-prepared",
            result=result,
        )
        return result


def _prepare_no_code_integration_run(request: dict[str, Any]) -> dict[str, Any]:
    ledger_path, topic_path, lock_path, owner_ref = _phase_request_context(
        request,
        {
            "source_checkpoint_id",
            "scope",
            "absorbed_relation_ids",
            "child_phase_result_ids",
        },
    )[:4]
    scope = _validated_string_list(request["scope"], "scope")
    relation_ids = _validated_string_list(
        request["absorbed_relation_ids"], "absorbed_relation_ids"
    )
    phase_result_ids = _validated_string_list(
        request["child_phase_result_ids"], "child_phase_result_ids"
    )
    with lock_path.open("a+b") as lock_stream:
        _flock_with_timeout(lock_stream)
        frontmatter, records = _load_records(ledger_path)
        replay = _idempotent_result(records, request)
        if replay is not None:
            return replay
        topic = _record_by_id(
            records["Current Topics"], "topic_id", request["actor_topic_id"], "topic_id"
        )
        ledger_revision, topic_revision = _validate_revisions(request, frontmatter, topic)
        _verify_topic_owner(records, request["actor_topic_id"], owner_ref)
        from_phase = topic.get("current_phase")
        if from_phase not in {1, 2}:
            raise ProtocolError(
                "phase_route_conflict",
                "no-code integration may enter phase 3 only from phase 1 or 2",
            )
        if _pending_topic_impacts(records, request["actor_topic_id"]):
            raise ProtocolError(
                "phase_impact_drift",
                "pending impacts must be resolved before no-code integration",
            )
        active_runs = [
            item
            for item in records["Phase Runs"]
            if item.get("run_kind") == "phase-run"
            and item.get("state")
            in {
                "prepared",
                "setup-pending",
                "ready",
                "active",
                "completion-claimed",
                "completion-pending",
                "outcome-unknown",
            }
            and _json_field(item, "data_json", "phase run").get("source_topic_id")
            == request["actor_topic_id"]
        ]
        if active_runs:
            raise ProtocolError(
                "phase_coordination_drift",
                "an active Phase Run already owns this source topic",
            )
        checkpoint = _completed_checkpoint(records, request["source_checkpoint_id"])
        if checkpoint.get("topic_id") != request["actor_topic_id"]:
            raise ProtocolError(
                "phase_checkpoint_invalid", "checkpoint belongs to another topic"
            )
        checkpoint_data = {
            "source_topic_id": request["actor_topic_id"],
            "source_checkpoint_id": checkpoint["checkpoint_id"],
            "source_checkpoint_identity": checkpoint["published_identity"],
        }
        _verify_wrapper_checkpoint_current(records, checkpoint_data, topic_path)

        relations = [
            _record_by_id(
                records["Relations and Coverage"],
                "relation_id",
                relation_id,
                "absorbed_relation_id",
            )
            for relation_id in relation_ids
        ]
        covered_scope: set[str] = set()
        child_topic_ids: set[str] = set()
        for relation in relations:
            if (
                relation.get("relation_type") != "absorbs"
                or relation.get("source_topic_id") != request["actor_topic_id"]
                or relation.get("state") != "active"
            ):
                raise ProtocolError(
                    "no_code_integration_invalid",
                    "coverage must come from active absorbed child results owned by the parent",
                )
            relation_scope = _json_field(relation, "scope_json", "absorbed relation")
            if not isinstance(relation_scope, list) or any(
                not isinstance(item, str) for item in relation_scope
            ):
                raise ProtocolError("state_corrupt", "absorbed relation scope is invalid")
            covered_scope.update(relation_scope)
            child_topic_ids.add(str(relation.get("target_topic_id")))
        if covered_scope != set(scope):
            raise ProtocolError(
                "no_code_integration_invalid",
                "absorbed child result scope does not exactly cover the parent integration scope",
            )

        result_topic_ids: set[str] = set()
        for phase_result_id in phase_result_ids:
            phase_result = _record_by_id(
                records["Phase Results"],
                "result_id",
                phase_result_id,
                "child_phase_result_id",
            )
            phase_result_data = _json_field(
                phase_result, "data_json", "child phase result"
            )
            if (
                phase_result.get("result_kind") != "phase-result"
                or phase_result.get("state") != "completed"
                or phase_result_data.get("to_phase") != 3
                or phase_result_data.get("topic_id") not in child_topic_ids
            ):
                raise ProtocolError(
                    "no_code_integration_invalid",
                    "every absorbed child must provide a completed phase-3 result",
                )
            result_topic_ids.add(str(phase_result_data["topic_id"]))
        if result_topic_ids != child_topic_ids:
            raise ProtocolError(
                "no_code_integration_invalid",
                "absorbed child topics and completed phase-3 results do not match",
            )
        for child_topic_id in child_topic_ids:
            child_topic = _record_by_id(
                records["Current Topics"], "topic_id", child_topic_id, "child_topic_id"
            )
            if child_topic.get("current_phase") != 3:
                raise ProtocolError(
                    "no_code_integration_invalid",
                    "an absorbed child topic is not currently at phase 3",
                )
        if any(
            item.get("state") in {"prepared", "queued", "active", "paused", "refresh-pending"}
            and item.get("topic_id") in child_topic_ids
            for item in records["Dependencies and Active Implementations"]
        ):
            raise ProtocolError(
                "phase_dependency_drift",
                "an absorbed child still has an active or uncertain implementation",
            )

        evidence = _authoritative_phase_evidence(topic_path, records, topic)
        run_id = f"PR-{ledger_revision + 1:08d}"
        if any(item.get("run_id") == run_id for item in records["Phase Runs"]):
            raise ProtocolError(
                "idempotency_conflict", "Phase Run creation identity already exists"
            )
        attempt_id = f"PA-{run_id[3:]}-1"
        data = {
            "run_id": run_id,
            "run_kind": "phase-run",
            "record_revision": 1,
            "state": "prepared",
            "from_phase": from_phase,
            "to_phase": 3,
            "route": [from_phase, 3],
            "carrier_kind": "integration-only",
            "source_topic_id": request["actor_topic_id"],
            "evidence": evidence,
            "attempts": [
                {
                    "attempt_id": attempt_id,
                    "attempt_number": 1,
                    "state": "setup-pending",
                    "authorization": False,
                    "carrier_ref": None,
                    "reason": None,
                    "claimed": False,
                }
            ],
            "creation_idempotency_key": request["idempotency_key"],
            "wrapper_integration": True,
            "source_checkpoint_id": checkpoint["checkpoint_id"],
            "source_checkpoint_identity": checkpoint["published_identity"],
            "flow_mode": "stepwise",
            "flow_mode_source": "explicit-stage-confirmation",
            "continuous_authorization_id": None,
            "scope": scope,
            "absorbed_relation_ids": relation_ids,
            "child_phase_result_ids": phase_result_ids,
            "implementation_mode": "no-code-integration",
            "requirement_document_mode": "frozen-read-only",
            "stage_ownership": ["integration-evidence"],
            "may_modify_requirement_source": False,
        }
        record = {
            "run_id": run_id,
            "run_kind": "phase-run",
            "state": "prepared",
            "record_revision": 1,
            "data_json": _canonical_json(data),
        }
        records["Phase Runs"].append(record)
        next_revision = ledger_revision + 1
        result = {
            "ok": True,
            "state": "prepared",
            "idempotent_replay": False,
            "project_id": request["project_id"],
            "tree_id": request["tree_id"],
            "topic_id": request["actor_topic_id"],
            "ledger_revision": next_revision,
            "record_revision": topic_revision,
            "phase_run_id": run_id,
            "attempt_id": attempt_id,
            "route": [from_phase, 3],
            "evidence": evidence,
            "source_checkpoint_id": checkpoint["checkpoint_id"],
            "source_checkpoint_identity": checkpoint["published_identity"],
            "implementation_mode": "no-code-integration",
            "scope": scope,
        }
        _write_ledger_transaction(
            ledger_path,
            frontmatter,
            records,
            request,
            ledger_revision=next_revision,
            event_type="no-code-integration-prepared",
            result=result,
        )
        return result


def _claim_phase_carrier(request: dict[str, Any]) -> dict[str, Any]:
    ledger_path, _, lock_path, owner_ref = _phase_request_context(
        request,
        {
            "phase_run_id", "attempt_id", "carrier_ref",
            "source_checkpoint_id", "source_checkpoint_identity",
        },
    )[:4]
    with lock_path.open("a+b") as lock_stream:
        _flock_with_timeout(lock_stream)
        frontmatter, records = _load_records(ledger_path)
        replay = _idempotent_result(records, request)
        if replay is not None:
            return replay
        topic = _record_by_id(records["Current Topics"], "topic_id", request["actor_topic_id"], "topic_id")
        ledger_revision, topic_revision = _validate_revisions(request, frontmatter, topic)
        record = _phase_record(records, request["phase_run_id"])
        data = _phase_data(record)
        _verify_phase_source(data, request["actor_topic_id"])
        attempt = _phase_attempt(data, request["attempt_id"])
        if data.get("wrapper_integration") is not True:
            raise ProtocolError("phase_identity_conflict", "carrier claims apply only to wrapper Phase Runs")
        if (
            data["state"] != "setup-pending"
            or attempt["state"] != "setup-pending"
            or attempt.get("authorization") is not True
            or attempt.get("claimed") is True
        ):
            raise ProtocolError("phase_attempt_state_conflict", "attempt is not claimable")
        carrier_ref = _expect_string(request["carrier_ref"], "carrier_ref", max_bytes=1024)
        if carrier_ref != owner_ref or carrier_ref != attempt.get("carrier_ref"):
            raise ProtocolError("phase_identity_conflict", "claimant does not match the authorized carrier")
        checkpoint = _completed_checkpoint(records, request["source_checkpoint_id"])
        if (
            checkpoint["checkpoint_id"] != data["source_checkpoint_id"]
            or checkpoint["published_identity"] != data["source_checkpoint_identity"]
            or request["source_checkpoint_identity"] != data["source_checkpoint_identity"]
        ):
            raise ProtocolError(
                "phase_checkpoint_invalid",
                "carrier checkpoint proof does not match the frozen source",
            )
        attempt["claimed"] = True
        data["record_revision"] += 1
        _store_phase(record, data)
        next_revision = ledger_revision + 1
        result = {
            "ok": True,
            "state": "setup-pending",
            "idempotent_replay": False,
            "project_id": request["project_id"],
            "tree_id": request["tree_id"],
            "topic_id": request["actor_topic_id"],
            "ledger_revision": next_revision,
            "record_revision": topic_revision,
            "phase_run_id": data["run_id"],
            "attempt_id": attempt["attempt_id"],
            "attempt_claimed": True,
            "source_checkpoint_id": data["source_checkpoint_id"],
        }
        _write_ledger_transaction(
            ledger_path,
            frontmatter,
            records,
            request,
            ledger_revision=next_revision,
            event_type="phase-carrier-claimed",
            result=result,
        )
        return result


def _authoritative_phase_evidence(
    topic_path: Path, records: dict[str, list[dict[str, Any]]], topic: dict[str, Any]
) -> dict[str, str]:
    topic_id = topic["topic_id"]
    dimensions = {
        "source": _sha256(_require_regular_nosymlink(topic_path, "topic document")),
        "route": _sha256(
            _canonical_json(
                {
                    "current_phase": topic["current_phase"],
                    "phase_state": topic["phase_state"],
                }
            ).encode("utf-8")
        ),
        "impact": _sha256(
            _canonical_json(
                [
                    item
                    for item in records["Impacts"]
                    if item.get("topic_id") == topic_id
                ]
            ).encode("utf-8")
        ),
        "coverage": _sha256(
            _canonical_json(
                [
                    item
                    for item in records["Relations and Coverage"]
                    if topic_id
                    in {item.get("source_topic_id"), item.get("target_topic_id")}
                ]
            ).encode("utf-8")
        ),
        "dependency": _sha256(
            _canonical_json(
                records["Dependencies and Active Implementations"]
            ).encode("utf-8")
        ),
        "coordination": _sha256(
            _canonical_json(
                [
                    item
                    for item in records["Conversation Bindings"]
                    if item.get("topic_id") == topic_id
                ]
            ).encode("utf-8")
        ),
    }
    return dimensions


def _prepare_phase_run(request: dict[str, Any]) -> dict[str, Any]:
    ledger_path, topic_path, lock_path, owner_ref = _phase_request_context(
        request, {"from_phase", "to_phase", "route", "carrier_kind"}
    )[:4]
    if not isinstance(request["from_phase"], int) or not isinstance(request["to_phase"], int):
        raise ProtocolError("invalid_request", "phase values must be integers")
    route = (request["from_phase"], request["to_phase"])
    if route not in PHASE_ROUTES:
        raise ProtocolError("invalid_phase_route", "only the six approved forward routes are legal")
    supplied_route = _expect_string(request["route"], "route", max_bytes=32)
    if supplied_route not in {f"{route[0]}->{route[1]}", f"{route[0]}\u2192{route[1]}"}:
        raise ProtocolError("phase_route_conflict", "route label does not match the requested phase transition")
    carrier_kind = _expect_string(request["carrier_kind"], "carrier_kind", max_bytes=64)
    with lock_path.open("a+b") as lock_stream:
        _flock_with_timeout(lock_stream)
        frontmatter, records = _load_records(ledger_path)
        replay = _idempotent_result(records, request)
        if replay is not None:
            return replay
        topic = _record_by_id(
            records["Current Topics"],
            "topic_id",
            request["actor_topic_id"],
            "topic_id",
        )
        ledger_revision, topic_revision = _validate_revisions(request, frontmatter, topic)
        _verify_topic_owner(records, request["actor_topic_id"], owner_ref)
        if topic.get("current_phase") != request["from_phase"]:
            raise ProtocolError("phase_route_conflict", "route source phase does not match current topic phase")
        active_runs = [
            item for item in records["Phase Runs"]
            if item.get("run_kind") == "phase-run"
            and item.get("state")
            in {
                "prepared",
                "setup-pending",
                "ready",
                "active",
                "completion-claimed",
                "completion-pending",
                "outcome-unknown",
            }
            and _json_field(item, "data_json", "phase run").get("source_topic_id") == request["actor_topic_id"]
        ]
        if active_runs:
            raise ProtocolError("phase_coordination_drift", "an active Phase Run already owns this source topic")
        evidence = _authoritative_phase_evidence(topic_path, records, topic)
        run_id = f"PR-{ledger_revision + 1:08d}"
        if any(item.get("run_id") == run_id for item in records["Phase Runs"]):
            raise ProtocolError("idempotency_conflict", "Phase Run creation identity already exists")
        attempt_id = f"PA-{run_id[3:]}-1"
        data = {
            "run_id": run_id,
            "run_kind": "phase-run",
            "record_revision": 1,
            "state": "prepared",
            "from_phase": request["from_phase"],
            "to_phase": request["to_phase"],
            "route": list(route),
            "carrier_kind": carrier_kind,
            "source_topic_id": request["actor_topic_id"],
            "evidence": evidence,
            "attempts": [
                {
                    "attempt_id": attempt_id,
                    "attempt_number": 1,
                    "state": "setup-pending",
                    "authorization": False,
                    "carrier_ref": None,
                    "reason": None,
                }
            ],
            "creation_idempotency_key": request["idempotency_key"],
        }
        record = {
            "run_id": run_id,
            "run_kind": "phase-run",
            "state": "prepared",
            "record_revision": 1,
            "data_json": _canonical_json(data),
        }
        records["Phase Runs"].append(record)
        next_revision = ledger_revision + 1
        result = {
            "ok": True,
            "state": "prepared",
            "idempotent_replay": False,
            "project_id": request["project_id"],
            "tree_id": request["tree_id"],
            "topic_id": request["actor_topic_id"],
            "ledger_revision": next_revision,
            "record_revision": topic_revision,
            "phase_run_id": run_id,
            "attempt_id": attempt_id,
            "route": list(route),
            "evidence": evidence,
        }
        _write_ledger_transaction(
            ledger_path,
            frontmatter,
            records,
            request,
            ledger_revision=next_revision,
            event_type="phase-run-prepared",
            result=result,
        )
        return result


def _retry_phase_run(request: dict[str, Any]) -> dict[str, Any]:
    ledger_path, _, lock_path, owner_ref = _phase_request_context(
        request,
        {"phase_run_id", "prior_attempt_id"},
    )[:4]
    with lock_path.open("a+b") as lock_stream:
        _flock_with_timeout(lock_stream)
        frontmatter, records = _load_records(ledger_path)
        replay = _idempotent_result(records, request)
        if replay is not None:
            return replay
        topic = _record_by_id(
            records["Current Topics"],
            "topic_id",
            request["actor_topic_id"],
            "topic_id",
        )
        ledger_revision, topic_revision = _validate_revisions(
            request,
            frontmatter,
            topic,
        )
        _verify_topic_owner(records, request["actor_topic_id"], owner_ref)
        record = _phase_record(records, request["phase_run_id"])
        data = _phase_data(record)
        _verify_phase_source(data, request["actor_topic_id"])
        prior = _phase_attempt(data, request["prior_attempt_id"])
        if data.get("state") != "failed" or prior["state"] != "failed" or prior is not data["attempts"][-1]:
            raise ProtocolError("phase_reconciliation_required", "only an explicitly failed attempt can be retried")
        number = len(data["attempts"]) + 1
        attempt_id = f"PA-{data['run_id'][3:]}-{number}"
        attempt = {
            "attempt_id": attempt_id,
            "attempt_number": number,
            "state": "setup-pending",
            "authorization": False,
            "carrier_ref": None,
            "reason": None,
        }
        data["attempts"].append(attempt)
        data["state"] = "prepared"
        data["record_revision"] += 1
        _store_phase(record, data)
        next_revision = ledger_revision + 1
        result = {
            "ok": True,
            "state": "prepared",
            "idempotent_replay": False,
            "project_id": request["project_id"],
            "tree_id": request["tree_id"],
            "topic_id": request["actor_topic_id"],
            "ledger_revision": next_revision,
            "record_revision": data["record_revision"],
            "topic_record_revision": topic_revision,
            "phase_run_id": data["run_id"],
            "attempt_id": attempt_id,
            "prior_attempt_id": prior["attempt_id"],
        }
        _write_ledger_transaction(
            ledger_path,
            frontmatter,
            records,
            request,
            ledger_revision=next_revision,
            event_type="phase-attempt-retried",
            result=result,
        )
        return result


def _reconcile_phase_run(request: dict[str, Any]) -> dict[str, Any]:
    ledger_path, topic_path, lock_path, owner_ref = _phase_request_context(
        request, {"phase_run_id", "attempt_id", "outcome", "reason", "evidence"}
    )[:4]
    outcome = _expect_string(request["outcome"], "outcome", max_bytes=64)
    if outcome not in {"not-created", "not-completed", "completed"}:
        raise ProtocolError("invalid_request", "phase reconciliation outcome is unsupported")
    with lock_path.open("a+b") as lock_stream:
        _flock_with_timeout(lock_stream)
        frontmatter, records = _load_records(ledger_path)
        replay = _idempotent_result(records, request)
        if replay is not None:
            return replay
        topic = _record_by_id(
            records["Current Topics"],
            "topic_id",
            request["actor_topic_id"],
            "topic_id",
        )
        ledger_revision, topic_revision = _validate_revisions(
            request,
            frontmatter,
            topic,
        )
        _verify_topic_owner(records, request["actor_topic_id"], owner_ref)
        record = _phase_record(records, request["phase_run_id"])
        data = _phase_data(record)
        _verify_phase_source(data, request["actor_topic_id"])
        attempt = _phase_attempt(data, request["attempt_id"])
        if attempt["state"] != "outcome-unknown":
            raise ProtocolError(
                "phase_reconciliation_required",
                "only outcome-unknown attempts can be reconciled",
            )
        if outcome == "completed":
            if attempt.get("authorization") is not True:
                raise ProtocolError(
                    "phase_authorization_required",
                    "an unauthorized unknown outcome cannot be completed",
                )
            _phase_check_evidence(data, _phase_evidence(request, required=True))
            _phase_check_evidence(data, _authoritative_phase_evidence(topic_path, records, topic))
            attempt["state"] = "completion-claimed"
            data["state"] = "completion-claimed"
        else:
            attempt["state"] = "failed"
            data["state"] = "failed"
        attempt["reason"] = request["reason"]
        data["record_revision"] += 1
        _store_phase(record, data)
        next_revision = ledger_revision + 1
        result = {
            "ok": True,
            "state": data["state"],
            "idempotent_replay": False,
            "project_id": request["project_id"],
            "tree_id": request["tree_id"],
            "topic_id": request["actor_topic_id"],
            "ledger_revision": next_revision,
            "record_revision": topic_revision,
            "phase_run_id": data["run_id"],
            "attempt_id": attempt["attempt_id"],
            "outcome": outcome,
        }
        _write_ledger_transaction(
            ledger_path,
            frontmatter,
            records,
            request,
            ledger_revision=next_revision,
            event_type="phase-run-reconciled",
            result=result,
        )
        return result


def _transition_phase_attempt(request: dict[str, Any], target: str, event_type: str) -> dict[str, Any]:
    allowed = {"phase_run_id", "attempt_id"}
    if target == "ready":
        allowed |= {"evidence", "carrier_ref"}
    elif target == "completion-claimed":
        allowed |= {"evidence", "carrier_ref"}
    elif target == "active":
        allowed |= {"evidence"}
    if target in {"failed", "blocked", "cancelled", "outcome-unknown"}:
        allowed |= {"reason"}
    ledger_path, topic_path, lock_path, owner_ref = _phase_request_context(request, allowed)[:4]
    supplied_evidence = _phase_evidence(
        request, required=target in {"ready", "active", "completion-claimed"}
    )
    with lock_path.open("a+b") as lock_stream:
        _flock_with_timeout(lock_stream)
        frontmatter, records = _load_records(ledger_path)
        replay = _idempotent_result(records, request)
        if replay is not None:
            return replay
        topic = _record_by_id(records["Current Topics"], "topic_id", request["actor_topic_id"], "topic_id")
        ledger_revision, topic_revision = _validate_revisions(request, frontmatter, topic)
        if target in {"active", "failed", "blocked", "cancelled", "outcome-unknown"}:
            _verify_topic_owner(records, request["actor_topic_id"], owner_ref)
        record = _phase_record(records, request["phase_run_id"])
        data = _phase_data(record)
        _verify_phase_source(data, request["actor_topic_id"])
        attempt = _phase_attempt(data, request["attempt_id"])
        if target == "ready":
            if data["state"] != "setup-pending" or attempt["state"] != "setup-pending":
                raise ProtocolError("phase_attempt_state_conflict", "attempt is not setup-pending")
            if attempt.get("authorization") is not True:
                raise ProtocolError(
                    "phase_authorization_required",
                    "carrier has not been authorized by the source topic",
                )
            if data.get("wrapper_integration") is True and attempt.get("claimed") is not True:
                raise ProtocolError(
                    "phase_carrier_claim_required",
                    "wrapper carrier must claim the frozen source before reporting ready",
                )
            if request["carrier_ref"] != attempt.get("carrier_ref") or attempt["carrier_ref"] != owner_ref:
                raise ProtocolError("phase_identity_conflict", "ready carrier identity does not match the caller")
            _phase_check_evidence(data, supplied_evidence)
            _phase_check_evidence(data, _authoritative_phase_evidence(topic_path, records, topic))
            attempt["state"] = "ready"
            data["state"] = "ready"
        elif target == "active":
            if data["state"] != "ready" or attempt["state"] != "ready":
                raise ProtocolError("phase_attempt_state_conflict", "attempt is not ready")
            _phase_check_evidence(data, supplied_evidence)
            _phase_check_evidence(data, _authoritative_phase_evidence(topic_path, records, topic))
            if data.get("wrapper_integration") is True:
                _verify_wrapper_checkpoint_current(records, data, topic_path)
                if data.get("flow_mode") == "continuous":
                    _verify_continuous_flow_authority(
                        records,
                        _completed_checkpoint(records, data["source_checkpoint_id"]),
                        data.get("continuous_authorization_id"),
                    )
            attempt["state"] = "active"
            data["state"] = "active"
            if data.get("wrapper_integration") is True and data.get("route") == [1, 3]:
                result_id = f"PH-{data['run_id'][3:]}-NA2"
                records["Phase Results"].append({
                    "result_id": result_id,
                    "result_kind": "phase-result",
                    "state": "not_applicable",
                    "record_revision": 1,
                    "data_json": _canonical_json({
                        "result_id": result_id,
                        "phase_run_id": data["run_id"],
                        "phase": 2,
                        "state": "not_applicable",
                        "scope": data["scope"],
                        "reason": (
                            "absorbed child implementation fully covers the integration scope"
                            if data.get("implementation_mode") == "no-code-integration"
                            else "stage-1 requirement completeness gate satisfied"
                        ),
                    }),
                })
        else:
            if target == "completion-claimed" and data["state"] != "active":
                raise ProtocolError(
                    "phase_attempt_state_conflict",
                    "completion can only be claimed by an active attempt",
                )
            if target == "outcome-unknown" and data["state"] not in {
                "active",
                "completion-claimed",
                "completion-pending",
            }:
                raise ProtocolError("phase_attempt_state_conflict", "unknown outcome requires an executing attempt")
            if target not in PHASE_ATTEMPT_STATES or attempt["state"] in {
                "completed",
                "cancelled",
                "failed",
                "blocked",
                "superseded",
            }:
                raise ProtocolError("phase_attempt_state_conflict", "terminal attempt cannot transition")
            if target == "completion-claimed" and attempt.get("authorization") is not True:
                raise ProtocolError("phase_authorization_required", "completion claim is no longer authorized")
            if target == "completion-claimed" and request["carrier_ref"] != attempt.get("carrier_ref"):
                raise ProtocolError("phase_identity_conflict", "completion claimant does not match the ready carrier")
            if target == "completion-claimed" and owner_ref != attempt.get("carrier_ref"):
                raise ProtocolError("phase_identity_conflict", "completion caller is not the ready carrier")
            if target == "completion-claimed":
                _phase_check_evidence(data, supplied_evidence)
            attempt["state"] = target
            attempt["reason"] = request.get("reason")
            data["state"] = target
        data["record_revision"] += 1
        _store_phase(record, data)
        next_revision = ledger_revision + 1
        result = {
            "ok": True,
            "state": target,
            "idempotent_replay": False,
            "project_id": request["project_id"],
            "tree_id": request["tree_id"],
            "topic_id": request["actor_topic_id"],
            "ledger_revision": next_revision,
            "record_revision": topic_revision,
            "phase_run_id": data["run_id"],
            "attempt_id": attempt["attempt_id"],
        }
        if target == "active" and data.get("wrapper_integration") is True and data.get("route") == [1, 3]:
            result.update({"not_applicable_phase": 2, "not_applicable_scope": data["scope"]})
        _write_ledger_transaction(
            ledger_path,
            frontmatter,
            records,
            request,
            ledger_revision=next_revision,
            event_type=event_type,
            result=result,
        )
        return result


def _revoke_phase_authorization(request: dict[str, Any]) -> dict[str, Any]:
    ledger_path, _, lock_path, owner_ref = _phase_request_context(
        request,
        {"phase_run_id", "attempt_id", "reason"},
    )[:4]
    with lock_path.open("a+b") as lock_stream:
        _flock_with_timeout(lock_stream)
        frontmatter, records = _load_records(ledger_path)
        replay = _idempotent_result(records, request)
        if replay is not None:
            return replay
        topic = _record_by_id(
            records["Current Topics"],
            "topic_id",
            request["actor_topic_id"],
            "topic_id",
        )
        ledger_revision, topic_revision = _validate_revisions(
            request,
            frontmatter,
            topic,
        )
        _verify_topic_owner(records, request["actor_topic_id"], owner_ref)
        record = _phase_record(records, request["phase_run_id"])
        data = _phase_data(record)
        _verify_phase_source(data, request["actor_topic_id"])
        attempt = _phase_attempt(data, request["attempt_id"])
        if attempt["state"] in {"completed", "cancelled", "failed", "blocked", "superseded"}:
            raise ProtocolError("phase_attempt_state_conflict", "terminal attempt cannot lose authorization")
        attempt["authorization"] = False
        attempt["state"] = "blocked"
        attempt["reason"] = request["reason"]
        data["state"] = "blocked"
        data["record_revision"] += 1
        _store_phase(record, data)
        next_revision = ledger_revision + 1
        result = {
            "ok": True,
            "state": "blocked",
            "idempotent_replay": False,
            "project_id": request["project_id"],
            "tree_id": request["tree_id"],
            "topic_id": request["actor_topic_id"],
            "ledger_revision": next_revision,
            "record_revision": topic_revision,
            "phase_run_id": data["run_id"],
            "attempt_id": attempt["attempt_id"],
        }
        _write_ledger_transaction(
            ledger_path,
            frontmatter,
            records,
            request,
            ledger_revision=next_revision,
            event_type="phase-authorization-revoked",
            result=result,
        )
        return result


def _authorize_phase_carrier(request: dict[str, Any]) -> dict[str, Any]:
    ledger_path, _, lock_path, owner_ref = _phase_request_context(
        request,
        {"phase_run_id", "attempt_id", "carrier_ref"},
    )[:4]
    carrier_ref = _expect_string(request["carrier_ref"], "carrier_ref", max_bytes=1024)
    with lock_path.open("a+b") as lock_stream:
        _flock_with_timeout(lock_stream)
        frontmatter, records = _load_records(ledger_path)
        replay = _idempotent_result(records, request)
        if replay is not None:
            return replay
        topic = _record_by_id(
            records["Current Topics"],
            "topic_id",
            request["actor_topic_id"],
            "topic_id",
        )
        ledger_revision, topic_revision = _validate_revisions(
            request,
            frontmatter,
            topic,
        )
        _verify_topic_owner(records, request["actor_topic_id"], owner_ref)
        record = _phase_record(records, request["phase_run_id"])
        data = _phase_data(record)
        _verify_phase_source(data, request["actor_topic_id"])
        attempt = _phase_attempt(data, request["attempt_id"])
        if data["state"] != "prepared" or attempt["state"] != "setup-pending" or attempt.get("authorization") is True:
            raise ProtocolError(
                "phase_attempt_state_conflict",
                "only a prepared setup-pending attempt can authorize one carrier",
            )
        attempt["authorization"] = True
        attempt["carrier_ref"] = carrier_ref
        data["state"] = "setup-pending"
        data["record_revision"] += 1
        _store_phase(record, data)
        next_revision = ledger_revision + 1
        result = {
            "ok": True,
            "state": "setup-pending",
            "idempotent_replay": False,
            "project_id": request["project_id"],
            "tree_id": request["tree_id"],
            "topic_id": request["actor_topic_id"],
            "ledger_revision": next_revision,
            "record_revision": data["record_revision"],
            "topic_record_revision": topic_revision,
            "phase_run_id": data["run_id"],
            "attempt_id": attempt["attempt_id"],
            "carrier_ref": carrier_ref,
        }
        _write_ledger_transaction(
            ledger_path,
            frontmatter,
            records,
            request,
            ledger_revision=next_revision,
            event_type="phase-carrier-authorized",
            result=result,
        )
        return result


def _claim_phase_completion(request: dict[str, Any]) -> dict[str, Any]:
    return _transition_phase_attempt(request, "completion-claimed", "phase-completion-claimed")


def _complete_phase_run(request: dict[str, Any]) -> dict[str, Any]:
    ledger_path, topic_path, lock_path, owner_ref = _phase_request_context(
        request,
        {"phase_run_id", "attempt_id", "evidence"},
    )[:4]
    supplied_evidence = _phase_evidence(request, required=True)
    with lock_path.open("a+b") as lock_stream:
        _flock_with_timeout(lock_stream)
        frontmatter, records = _load_records(ledger_path)
        replay = _idempotent_result(records, request)
        if replay is not None:
            return replay
        topic = _record_by_id(records["Current Topics"], "topic_id", request["actor_topic_id"], "topic_id")
        ledger_revision, topic_revision = _validate_revisions(request, frontmatter, topic)
        _verify_topic_owner(records, request["actor_topic_id"], owner_ref)
        record = _phase_record(records, request["phase_run_id"])
        data = _phase_data(record)
        _verify_phase_source(data, request["actor_topic_id"])
        attempt = _phase_attempt(data, request["attempt_id"])
        if data["state"] != "completion-claimed" or attempt["state"] != "completion-claimed":
            raise ProtocolError("phase_completion_not_claimed", "completion must be claimed before acceptance")
        _phase_check_evidence(data, supplied_evidence)
        _phase_check_evidence(data, _authoritative_phase_evidence(topic_path, records, topic))
        attempt["state"] = "completion-pending"
        data["state"] = "completion-pending"
        data["record_revision"] += 1
        _store_phase(record, data)
        pending_revision = ledger_revision + 1
        pending_result = {
            "ok": True,
            "state": "completion-pending",
            "idempotent_replay": False,
            "project_id": request["project_id"],
            "tree_id": request["tree_id"],
            "topic_id": request["actor_topic_id"],
            "ledger_revision": pending_revision,
            "record_revision": topic_revision,
            "phase_run_id": data["run_id"],
            "attempt_id": attempt["attempt_id"],
        }
        _write_ledger_transaction(
            ledger_path,
            frontmatter,
            records,
            request,
            ledger_revision=pending_revision,
            event_type="phase-completion-pending",
            result=pending_result,
        )
        return pending_result


def _finalize_phase_run(request: dict[str, Any]) -> dict[str, Any]:
    ledger_path, topic_path, lock_path, owner_ref = _phase_request_context(
        request,
        {"phase_run_id", "attempt_id", "evidence"},
    )[:4]
    supplied_evidence = _phase_evidence(request, required=True)
    with lock_path.open("a+b") as lock_stream:
        _flock_with_timeout(lock_stream)
        frontmatter, records = _load_records(ledger_path)
        replay = _idempotent_result(records, request)
        if replay is not None:
            return replay
        topic = _record_by_id(
            records["Current Topics"],
            "topic_id",
            request["actor_topic_id"],
            "topic_id",
        )
        ledger_revision, _ = _validate_revisions(request, frontmatter, topic)
        _verify_topic_owner(records, request["actor_topic_id"], owner_ref)
        record = _phase_record(records, request["phase_run_id"])
        data = _phase_data(record)
        _verify_phase_source(data, request["actor_topic_id"])
        attempt = _phase_attempt(data, request["attempt_id"])
        if data["state"] != "completion-pending" or attempt["state"] != "completion-pending":
            raise ProtocolError("phase_run_state_conflict", "only completion-pending Phase Runs can be finalized")
        _phase_check_evidence(data, supplied_evidence)
        _phase_check_evidence(data, _authoritative_phase_evidence(topic_path, records, topic))
        attempt["state"] = "completed"
        data["state"] = "completed"
        data["record_revision"] += 1
        topic["current_phase"] = data["to_phase"]
        topic["phase_state"] = "active"
        topic["record_revision"] = int(topic["record_revision"]) + 1
        result_id = f"PH-{data['run_id'][3:]}"
        affected_decision_ids = sorted(
            item["decision_id"]
            for item in _topic_snapshot(records, request["actor_topic_id"])["decisions"]
            if item.get("state") != "discarded"
        )
        phase_result_data = {
            "result_id": result_id,
            "phase_run_id": data["run_id"],
            "topic_id": request["actor_topic_id"],
            "from_phase": data["from_phase"],
            "to_phase": data["to_phase"],
            "affected_decision_ids": affected_decision_ids,
            "evidence": data["evidence"],
        }
        if data.get("implementation_mode") is not None:
            phase_result_data.update(
                {
                    "implementation_mode": data["implementation_mode"],
                    "scope": data["scope"],
                    "absorbed_relation_ids": data["absorbed_relation_ids"],
                    "child_phase_result_ids": data["child_phase_result_ids"],
                }
            )
        records["Phase Results"].append(
            {
                "result_id": result_id,
                "result_kind": "phase-result",
                "state": "completed",
                "record_revision": 1,
                "data_json": _canonical_json(phase_result_data),
            }
        )
        _store_phase(record, data)
        next_revision = ledger_revision + 1
        result = {
            "ok": True,
            "state": "completed",
            "idempotent_replay": False,
            "project_id": request["project_id"],
            "tree_id": request["tree_id"],
            "topic_id": request["actor_topic_id"],
            "ledger_revision": next_revision,
            "record_revision": topic["record_revision"],
            "phase_run_id": data["run_id"],
            "attempt_id": attempt["attempt_id"],
            "phase_result_id": result_id,
            "current_phase": topic["current_phase"],
        }
        if data.get("implementation_mode") is not None:
            result["implementation_mode"] = data["implementation_mode"]
        _write_ledger_transaction(
            ledger_path,
            frontmatter,
            records,
            request,
            ledger_revision=next_revision,
            event_type="phase-run-completed",
            result=result,
        )
        return result


def _supersede_phase_run(request: dict[str, Any]) -> dict[str, Any]:
    ledger_path, _, lock_path, owner_ref = _phase_request_context(
        request, {"phase_run_id", "attempt_id", "reason"}
    )[:4]
    reason = _expect_string(request["reason"], "reason", max_bytes=4096)
    with lock_path.open("a+b") as lock_stream:
        _flock_with_timeout(lock_stream)
        frontmatter, records = _load_records(ledger_path)
        replay = _idempotent_result(records, request)
        if replay is not None:
            return replay
        topic = _record_by_id(
            records["Current Topics"], "topic_id", request["actor_topic_id"], "topic_id"
        )
        ledger_revision, topic_revision = _validate_revisions(request, frontmatter, topic)
        _verify_topic_owner(records, request["actor_topic_id"], owner_ref)
        record = _phase_record(records, request["phase_run_id"])
        data = _phase_data(record)
        _verify_phase_source(data, request["actor_topic_id"])
        attempt = _phase_attempt(data, request["attempt_id"])
        terminal = {"completed", "cancelled", "failed", "blocked", "superseded"}
        if data["state"] in terminal or attempt["state"] in terminal:
            raise ProtocolError(
                "phase_attempt_state_conflict", "terminal Phase Run cannot be superseded"
            )
        attempt.update({"state": "superseded", "authorization": False, "reason": reason})
        data["state"] = "superseded"
        data["record_revision"] += 1
        _store_phase(record, data)
        next_revision = ledger_revision + 1
        result = {
            "ok": True,
            "state": "superseded",
            "idempotent_replay": False,
            "project_id": request["project_id"],
            "tree_id": request["tree_id"],
            "topic_id": request["actor_topic_id"],
            "ledger_revision": next_revision,
            "record_revision": topic_revision,
            "phase_run_id": data["run_id"],
            "attempt_id": attempt["attempt_id"],
        }
        _write_ledger_transaction(
            ledger_path,
            frontmatter,
            records,
            request,
            ledger_revision=next_revision,
            event_type="phase-run-superseded",
            result=result,
        )
        return result


def _read_phase_run(request: dict[str, Any]) -> dict[str, Any]:
    ledger_path, _, lock_path, _ = _phase_request_context(
        request,
        {"phase_run_id"},
        query=True,
    )[:4]
    with lock_path.open("a+b") as lock_stream:
        _flock_with_timeout(lock_stream)
        frontmatter, records = _load_records(ledger_path)
        data = _phase_data(_phase_record(records, request["phase_run_id"]))
        return {
            "ok": True,
            "state": "read",
            "ledger_revision": int(frontmatter["ledger_revision"]),
            "phase_run": data,
        }


def _route_phase(request: dict[str, Any]) -> dict[str, Any]:
    return _prepare_phase_run(request)


def _reopen_phase(request: dict[str, Any]) -> dict[str, Any]:
    ledger_path, _, lock_path, owner_ref = _phase_request_context(
        request,
        {"affected_decision_ids", "review", "reason"},
    )[:4]
    affected = request["affected_decision_ids"]
    review = request["review"]
    if (
        not isinstance(affected, list)
        or not affected
        or any(not isinstance(item, str) for item in affected)
        or len(set(affected)) != len(affected)
        or not isinstance(review, dict)
        or set(review) != set(affected)
    ):
        raise ProtocolError(
            "phase_reopen_review_required",
            "reopen requires affected_decision_ids",
        )
    if any(action not in {"keep", "adjust", "replace", "discard"} for action in review.values()):
        raise ProtocolError(
            "phase_reopen_review_required",
            "each affected decision requires an explicit review action",
        )
    with lock_path.open("a+b") as lock_stream:
        _flock_with_timeout(lock_stream)
        frontmatter, records = _load_records(ledger_path)
        replay = _idempotent_result(records, request)
        if replay is not None:
            return replay
        topic = _record_by_id(
            records["Current Topics"],
            "topic_id",
            request["actor_topic_id"],
            "topic_id",
        )
        ledger_revision, topic_revision = _validate_revisions(
            request,
            frontmatter,
            topic,
        )
        _verify_topic_owner(records, request["actor_topic_id"], owner_ref)
        if topic.get("current_phase") == 0:
            raise ProtocolError("phase_route_conflict", "topic is already in phase 0")
        active_runs = [
            item for item in records["Phase Runs"]
            if item.get("run_kind") == "phase-run"
            and item.get("state")
            in {
                "prepared",
                "setup-pending",
                "ready",
                "active",
                "completion-claimed",
                "completion-pending",
                "outcome-unknown",
            }
            and _json_field(item, "data_json", "phase run").get("source_topic_id") == request["actor_topic_id"]
        ]
        if active_runs:
            raise ProtocolError(
                "phase_coordination_drift",
                "active Phase Runs must be cancelled or reconciled before reopen",
            )
        topic_snapshot = _topic_snapshot(records, request["actor_topic_id"])
        known = {item["decision_id"] for item in topic_snapshot["decisions"]}
        authoritative_affected = {
            item["decision_id"]
            for item in topic_snapshot["decisions"]
            if item.get("state") != "discarded"
        }
        authoritative_affected.update(
            item["decision_id"] for item in topic_snapshot["impacts"]
        )
        review_pending_results = []
        for result_record in records["Phase Results"]:
            if result_record.get("result_kind") not in {
                "phase-result",
                "imported-phase-result",
            }:
                continue
            result_data = _json_field(result_record, "data_json", "phase result")
            result_topic_id = result_data.get("topic_id")
            if result_topic_id is None and result_record.get("result_kind") == "phase-result":
                phase_run = _phase_data(
                    _phase_record(records, result_data.get("phase_run_id"))
                )
                result_topic_id = phase_run.get("source_topic_id")
            if result_topic_id != request["actor_topic_id"]:
                continue
            result_decisions = result_data.get("affected_decision_ids")
            if isinstance(result_decisions, list) and all(
                isinstance(item, str) for item in result_decisions
            ):
                authoritative_affected.update(result_decisions)
            else:
                authoritative_affected.update(known)
            if result_record.get("state") == "completed":
                review_pending_results.append((result_record, result_data))
        if set(affected) != authoritative_affected or not authoritative_affected <= known:
            raise ProtocolError(
                "phase_reopen_review_required",
                "affected_decision_ids must exactly match authoritative topic results and impacts",
            )
        for item in records["Pending Items"]:
            if item.get("item_kind") == "decision" and item.get("item_id") in review:
                decision = _json_field(item, "data_json", "decision")
                decision["reopen_review"] = review[item["item_id"]]
                item["data_json"] = _canonical_json(decision)
        review_pending_result_ids = []
        for result_record, result_data in review_pending_results:
            result_record["state"] = "review-pending"
            result_record["record_revision"] = int(result_record["record_revision"]) + 1
            result_data["review_state"] = "pending"
            result_data["reopen_affected_decision_ids"] = sorted(authoritative_affected)
            result_record["data_json"] = _canonical_json(result_data)
            review_pending_result_ids.append(result_record["result_id"])
        topic["current_phase"] = 0
        topic["phase_state"] = "active"
        topic["record_revision"] = int(topic["record_revision"]) + 1
        next_revision = ledger_revision + 1
        result = {
            "ok": True,
            "state": "reopened",
            "idempotent_replay": False,
            "project_id": request["project_id"],
            "tree_id": request["tree_id"],
            "topic_id": request["actor_topic_id"],
            "ledger_revision": next_revision,
            "record_revision": topic["record_revision"],
            "current_phase": 0,
            "affected_decision_ids": sorted(authoritative_affected),
            "review_pending_result_ids": sorted(review_pending_result_ids),
        }
        _write_ledger_transaction(
            ledger_path,
            frontmatter,
            records,
            request,
            ledger_revision=next_revision,
            event_type="phase-reopened",
            result=result,
        )
        return result


def _discover_context(request: dict[str, Any]) -> dict[str, Any]:
    allowed = {
        "protocol_version",
        "operation",
        "project_path",
        "authenticated_identity",
        "conversation_ref",
        "document_path",
        "footer_identity",
    }
    if any(key not in allowed for key in request):
        raise ProtocolError("invalid_request", "discover-context request contains unsupported evidence")
    project = _validate_project_path(request["project_path"])
    strong_identities = [
        item
        for item in (
            request.get("authenticated_identity"),
            request.get("footer_identity"),
        )
        if item is not None
    ]
    for identity in strong_identities:
        if not isinstance(identity, dict) or set(identity) != {"project_id", "tree_id", "topic_id"}:
            raise ProtocolError("invalid_request", "strong identity evidence has an invalid shape")
    if len(strong_identities) == 2 and strong_identities[0] != strong_identities[1]:
        raise ProtocolError("discussion_identity_conflict", "strong lifecycle identities conflict")
    document_evidence = request.get("document_path")
    evidence_path = None
    if document_evidence is not None:
        evidence_path = Path(_expect_string(document_evidence, "document_path", max_bytes=4096))
        try:
            evidence_path.relative_to(project)
        except ValueError as error:
            raise ProtocolError("invalid_request", "document_path must be inside the project") from error
        if not evidence_path.is_file():
            raise ProtocolError("discussion_identity_conflict", "document_path evidence does not exist")
    manifest_path = project / "docs" / "discussions" / ".codex-project.md"
    if not manifest_path.exists():
        discussion_root = project / "docs" / "discussions"
        candidates = sorted(
            path for path in discussion_root.glob("*/topic.md")
            if path.is_file()
        ) if discussion_root.is_dir() else []
        if len(candidates) == 1 and evidence_path is not None:
            topic_path = candidates[0]
            if evidence_path is not None and evidence_path != topic_path:
                raise ProtocolError(
                    "discussion_identity_conflict",
                    "document evidence conflicts with the unique topic",
                )
            topic_frontmatter = _parse_frontmatter(
                _require_regular_nosymlink(topic_path, "topic document"),
                "topic document",
            )
            observed = {
                key: topic_frontmatter.get(key)
                for key in ("project_id", "tree_id", "topic_id")
            }
            if any(value is None for value in observed.values()) or any(
                identity != observed for identity in strong_identities
            ):
                raise ProtocolError(
                    "discussion_identity_conflict",
                    "strong evidence conflicts with the unique document",
                )
            return {
                "ok": True,
                "state": "discovered",
                "context": "document_only",
                "created": False,
                "candidate_count": 1,
                "topic_document_path": str(topic_path),
                "project_id": topic_frontmatter.get("project_id"),
                "tree_id": topic_frontmatter.get("tree_id"),
                "topic_id": topic_frontmatter.get("topic_id"),
                "coordination_state": "unknown",
            }
        return {
            "ok": True,
            "state": "ambiguous" if len(candidates) > 1 else "none",
            "context": "ambiguous" if len(candidates) > 1 else "none",
            "candidate_count": len(candidates),
            "created": False,
        }
    manifest = _parse_frontmatter(
        _require_regular_nosymlink(manifest_path, "project identity manifest"),
        "project identity manifest",
    )
    if not all(key in manifest for key in ("project_id", "tree_id", "topic_id", "root_slug")):
        raise ProtocolError("context_identity_conflict", "project identity manifest is incomplete")
    coordination_root, storage_mode = _coordination_root(project)
    ledger_path = coordination_root / "projects" / manifest["project_id"] / "trees" / manifest["tree_id"] / "ledger.md"
    topic_path = project / "docs" / "discussions" / manifest["root_slug"] / "topic.md"
    if ledger_path.exists():
        _, records = _load_records(ledger_path)
        selected_topic_id = None
        for strong in strong_identities:
            if any(strong.get(key) != manifest[key] for key in ("project_id", "tree_id")):
                raise ProtocolError(
                    "discussion_identity_conflict",
                    "strong lifecycle evidence conflicts with the ledger identity",
                )
            strong_topics = [
                item
                for item in records["Current Topics"]
                if item.get("topic_id") == strong["topic_id"]
            ]
            if len(strong_topics) != 1:
                raise ProtocolError(
                    "discussion_identity_conflict",
                    "strong lifecycle evidence does not identify one ledger topic",
                )
            selected_topic_id = strong["topic_id"]
        if request.get("conversation_ref") is not None:
            ref = _expect_string(request["conversation_ref"], "conversation_ref", max_bytes=1024)
            active_bindings = [
                item
                for item in records["Conversation Bindings"]
                if item.get("conversation_ref") == ref
                and item.get("binding_state") == "active"
            ]
            if len(active_bindings) != 1:
                raise ProtocolError(
                    "discussion_identity_conflict",
                    "conversation evidence is not one active binding",
                )
            binding_topic_id = active_bindings[0].get("topic_id")
            if selected_topic_id is not None and binding_topic_id != selected_topic_id:
                raise ProtocolError(
                    "discussion_identity_conflict",
                    "conversation evidence conflicts with stronger lifecycle evidence",
                )
            selected_topic_id = binding_topic_id
        if evidence_path is not None:
            document_topics = [
                item
                for item in records["Current Topics"]
                if item.get("topic_document_path") == str(evidence_path)
            ]
            if len(document_topics) != 1:
                raise ProtocolError(
                    "discussion_identity_conflict",
                    "document evidence does not identify one ledger topic",
                )
            document_topic_id = document_topics[0].get("topic_id")
            if selected_topic_id is not None and document_topic_id != selected_topic_id:
                raise ProtocolError(
                    "discussion_identity_conflict",
                    "document evidence conflicts with stronger lifecycle evidence",
                )
            selected_topic_id = document_topic_id
        if selected_topic_id is None:
            selected_topic_id = manifest["topic_id"]
        selected_topic = _record_by_id(
            records["Current Topics"],
            "topic_id",
            selected_topic_id,
            "discovered topic_id",
        )
        selected_topic_path = selected_topic.get("topic_document_path")
        return {
            "ok": True,
            "state": "discovered",
            "context": "ledger",
            "storage_mode": storage_mode,
            "project_id": manifest["project_id"],
            "tree_id": manifest["tree_id"],
            "topic_id": selected_topic_id,
            "ledger_path": str(ledger_path),
            "topic_document_path": selected_topic_path,
            "created": False,
        }
    candidate_paths = sorted(
        path
        for path in (project / "docs" / "discussions").glob("*/topic.md")
        if path.is_file()
    )
    selected_path = None
    selected_identity = None
    if strong_identities:
        strong_identity = strong_identities[0]
        if any(strong_identity[key] != manifest[key] for key in ("project_id", "tree_id")):
            raise ProtocolError(
                "discussion_identity_conflict",
                "strong lifecycle evidence conflicts with the project manifest",
            )
        matching_documents = []
        for candidate_path in candidate_paths:
            candidate_identity = _parse_frontmatter(
                _require_regular_nosymlink(candidate_path, "topic document"),
                "topic document",
            )
            if all(
                candidate_identity.get(key) == strong_identity[key]
                for key in ("project_id", "tree_id", "topic_id")
            ):
                matching_documents.append((candidate_path, candidate_identity))
        if len(matching_documents) != 1:
            raise ProtocolError(
                "discussion_identity_conflict",
                "strong lifecycle evidence does not identify one topic document",
            )
        selected_path, selected_identity = matching_documents[0]
    if evidence_path is not None:
        if evidence_path not in candidate_paths:
            raise ProtocolError(
                "discussion_identity_conflict",
                "document evidence does not identify one topic document",
            )
        document_identity = _parse_frontmatter(
            _require_regular_nosymlink(evidence_path, "topic document"),
            "topic document",
        )
        if any(document_identity.get(key) != manifest[key] for key in ("project_id", "tree_id")):
            raise ProtocolError(
                "discussion_identity_conflict",
                "document evidence conflicts with the project manifest",
            )
        if selected_identity is not None and any(
            document_identity.get(key) != selected_identity.get(key)
            for key in ("project_id", "tree_id", "topic_id")
        ):
            raise ProtocolError(
                "discussion_identity_conflict",
                "document evidence conflicts with stronger lifecycle evidence",
            )
        selected_path, selected_identity = evidence_path, document_identity
    if selected_path is None and topic_path.exists():
        selected_path = topic_path
        selected_identity = _parse_frontmatter(
            _require_regular_nosymlink(topic_path, "topic document"),
            "topic document",
        )
        if any(
            selected_identity.get(key) != manifest[key]
            for key in ("project_id", "tree_id", "topic_id")
        ):
            raise ProtocolError(
                "discussion_identity_conflict",
                "document identity conflicts with the project manifest",
            )
    if selected_path is not None and selected_identity is not None:
        return {
            "ok": True,
            "state": "discovered",
            "context": "document_only",
            "storage_mode": storage_mode,
            "project_id": manifest["project_id"],
            "tree_id": manifest["tree_id"],
            "topic_id": selected_identity["topic_id"],
            "topic_document_path": str(selected_path),
            "coordination_state": "unknown",
            "created": False,
        }
    return {"ok": True, "state": "none", "context": "none", "created": False}


def _initialize_document_context(request: dict[str, Any]) -> dict[str, Any]:
    base_fields = {
        "protocol_version", "operation", "project_path", "conversation_ref",
        "idempotency_key", "user_authorization", "verified_results",
    }
    topic_fields = {"topic_identity", "topic_document_path"}
    expected_fields = base_fields | topic_fields if set(request) & topic_fields else base_fields
    _expect_keys(
        request,
        expected_fields,
        "initialize-document-context request",
    )
    project = _validate_project_path(request["project_path"])
    if request["user_authorization"] is not True:
        raise ProtocolError(
            "context_not_initialized",
            "document_only initialization requires explicit user_authorization=true",
        )
    conversation_ref = _expect_string(request["conversation_ref"], "conversation_ref")
    key = _validate_uuid4(request["idempotency_key"], "idempotency_key")
    manifest_path = project / "docs" / "discussions" / ".codex-project.md"
    manifest = _parse_frontmatter(
        _require_regular_nosymlink(manifest_path, "project identity manifest"),
        "project identity manifest",
    )
    for field in ("project_id", "tree_id", "topic_id", "root_slug"):
        if field not in manifest:
            raise ProtocolError(
                "context_identity_conflict",
                "document-only manifest is incomplete",
            )
    explicit_topic = "topic_identity" in request
    if explicit_topic:
        topic_identity = request["topic_identity"]
        if not isinstance(topic_identity, dict) or set(topic_identity) != {
            "project_id", "tree_id", "topic_id",
        }:
            raise ProtocolError("invalid_request", "topic_identity has an invalid shape")
        for field, kind in (
            ("project_id", "project"),
            ("tree_id", "tree"),
            ("topic_id", "topic"),
        ):
            value = _expect_string(topic_identity[field], f"topic_identity.{field}")
            if not value.startswith(f"{kind}-") or not IDENTITY_RE.fullmatch(value):
                raise ProtocolError(
                    "discussion_identity_conflict",
                    f"topic_identity.{field} is invalid",
                )
        if any(topic_identity[field] != manifest[field] for field in ("project_id", "tree_id")):
            raise ProtocolError(
                "context_identity_conflict",
                "topic identity conflicts with the project manifest",
            )
        topic_path = Path(
            _expect_string(
                request["topic_document_path"],
                "topic_document_path",
                max_bytes=4096,
            )
        )
        try:
            resolved_topic_path = topic_path.resolve(strict=True)
        except OSError as error:
            raise ProtocolError(
                "context_identity_conflict",
                "topic document is missing",
                cause=str(error),
            ) from error
        if topic_path != resolved_topic_path:
            raise ProtocolError(
                "invalid_request",
                "topic_document_path must be canonical and contain no symbolic-link or relative components",
            )
        try:
            topic_relative_path = topic_path.relative_to(project / "docs" / "discussions")
        except ValueError as error:
            raise ProtocolError(
                "invalid_request",
                "topic_document_path must be inside the project's discussion documents",
            ) from error
        if len(topic_relative_path.parts) != 2 or topic_relative_path.name != "topic.md":
            raise ProtocolError(
                "context_identity_conflict",
                "topic_document_path does not identify one discussion topic document",
            )
        topic_root_slug = topic_relative_path.parts[0]
        if not ROOT_SLUG_RE.fullmatch(topic_root_slug):
            raise ProtocolError(
                "context_identity_conflict",
                "topic document slug is invalid",
            )
        selected_identity = {
            field: topic_identity[field]
            for field in ("project_id", "tree_id", "topic_id")
        }
    else:
        topic_path = project / "docs" / "discussions" / manifest["root_slug"] / "topic.md"
        topic_root_slug = manifest["root_slug"]
        selected_identity = {
            field: manifest[field]
            for field in ("project_id", "tree_id", "topic_id")
        }
    if not topic_path.exists():
        raise ProtocolError(
            "context_identity_conflict",
            "document-only topic document is missing",
        )
    topic_data = _require_regular_nosymlink(topic_path, "topic document")
    topic_frontmatter = _parse_frontmatter(topic_data, "topic document")
    for field in ("project_id", "tree_id", "topic_id"):
        if topic_frontmatter.get(field) != selected_identity[field]:
            raise ProtocolError(
                "context_identity_conflict",
                "document-only topic identity conflicts with the requested context",
            )
    verified_results = request["verified_results"]
    if not isinstance(verified_results, list):
        raise ProtocolError("invalid_request", "verified_results must be an array")
    imported_results = []
    for item in verified_results:
        if not isinstance(item, dict) or set(item) != {"result_id", "phase", "state", "path", "sha256"}:
            raise ProtocolError("invalid_request", "verified result has an invalid shape")
        result_id = _expect_string(item["result_id"], "verified result_id", max_bytes=64)
        phase = item["phase"]
        if not isinstance(phase, int) or isinstance(phase, bool) or phase not in range(5):
            raise ProtocolError(
                "invalid_request",
                "verified result phase must be an integer from 0 through 4",
            )
        state = _expect_string(item["state"], "verified result state", max_bytes=64)
        if state != "completed":
            raise ProtocolError(
                "invalid_request",
                "only completed stable phase results can be imported",
            )
        result_path = Path(_expect_string(item["path"], "verified result path", max_bytes=4096))
        try:
            result_path.relative_to(project)
        except ValueError as error:
            raise ProtocolError("invalid_request", "verified result path must be inside the project") from error
        result_bytes = _require_regular_nosymlink(result_path, "verified phase result")
        if not SHA256_RE.fullmatch(str(item["sha256"])) or _sha256(result_bytes) != item["sha256"]:
            raise ProtocolError(
                "context_identity_conflict",
                "verified phase result digest does not match",
            )
        result_metadata = _parse_frontmatter(result_bytes, "verified phase result")
        expected_metadata = {
            "project_id": selected_identity["project_id"],
            "tree_id": selected_identity["tree_id"],
            "topic_id": selected_identity["topic_id"],
            "result_id": result_id,
            "phase": str(phase),
            "state": state,
        }
        if any(result_metadata.get(field) != value for field, value in expected_metadata.items()):
            raise ProtocolError(
                "context_identity_conflict",
                "verified phase result metadata does not match its requested identity",
            )
        imported_results.append(
            {
                **item,
                **selected_identity,
            }
        )
    imported_result_ids = [item["result_id"] for item in imported_results]
    if len(imported_result_ids) != len(set(imported_result_ids)):
        raise ProtocolError(
            "invalid_request",
            "verified result identities must be unique",
        )
    coordination_root, storage_mode = _coordination_root(project)
    ledger_path = coordination_root / "projects" / manifest["project_id"] / "trees" / manifest["tree_id"] / "ledger.md"
    fingerprint = _sha256(
        _canonical_json(
            {
                "conversation_ref": conversation_ref,
                "entry_mode": "document-only",
                "project_path": str(project),
                "root_slug": topic_root_slug,
                "verified_results": imported_results,
                **(
                    {
                        "topic_identity": selected_identity,
                        "topic_document_path": str(topic_path),
                    }
                    if explicit_topic
                    else {}
                ),
            }
        ).encode("utf-8")
    )
    if ledger_path.exists():
        frontmatter, records = _load_records(ledger_path)
        events = [event for event in records["Recent Events"] if event.get("idempotency_key") == key]
        if not events:
            raise ProtocolError(
                "context_not_initialized",
                "document-only context is already initialized with a different authorization",
            )
        if events[-1].get("request_fingerprint") != fingerprint:
            raise ProtocolError(
                "idempotency_conflict",
                "document-only initialization key was reused with different parameters",
            )
        return {
            "ok": True,
            "state": "initialized",
            "context": "ledger",
            "created": False,
            "storage_mode": storage_mode,
            "project_id": manifest["project_id"],
            "tree_id": manifest["tree_id"],
            "topic_id": selected_identity["topic_id"],
            "ledger_revision": int(frontmatter["ledger_revision"]),
            "topic_revision": 1,
            "ledger_path": str(ledger_path),
            "topic_document_path": str(topic_path),
            "imported_result_count": len(imported_results),
            "coordination_state": "unknown",
            "idempotent_replay": True,
        }
    lock_path = coordination_root / "locks" / _project_lock_name(project)
    created_directories: list[Path] = []
    created_files: list[Path] = []
    try:
        _mkdirs(ledger_path.parent, created_directories)
        _mkdirs(lock_path.parent, created_directories)
        lock_stream = lock_path.open("a+b")
        try:
            fcntl.flock(lock_stream.fileno(), fcntl.LOCK_EX)
            if ledger_path.exists():
                frontmatter, records = _load_records(ledger_path)
                events = [
                    event
                    for event in records["Recent Events"]
                    if event.get("idempotency_key") == key
                ]
                if not events:
                    raise ProtocolError(
                        "context_not_initialized",
                        "document-only context was concurrently initialized by another authorization",
                    )
                if events[-1].get("request_fingerprint") != fingerprint:
                    raise ProtocolError(
                        "idempotency_conflict",
                        "document-only initialization key was reused with different parameters",
                    )
                return {
                    "ok": True,
                    "state": "initialized",
                    "context": "ledger",
                    "created": False,
                    "storage_mode": storage_mode,
                    "project_id": manifest["project_id"],
                    "tree_id": manifest["tree_id"],
                    "topic_id": selected_identity["topic_id"],
                    "ledger_revision": int(frontmatter["ledger_revision"]),
                    "topic_revision": 1,
                    "ledger_path": str(ledger_path),
                    "topic_document_path": str(topic_path),
                    "imported_result_count": len(imported_results),
                    "coordination_state": "unknown",
                    "idempotent_replay": True,
                }
            data = _render_ledger(
                project_id=manifest["project_id"],
                tree_id=manifest["tree_id"],
                topic_id=selected_identity["topic_id"],
                root_slug=topic_root_slug,
                conversation_ref=conversation_ref,
                idempotency_key=key,
                request_fingerprint=fingerprint,
                project_manifest_path=manifest_path,
                topic_document_path=topic_path,
            )
            if imported_results:
                frontmatter, text = _verify_ledger_digest(data)
                sections = _ledger_sections(text)
                records = {
                    name: _parse_record_section(sections[name], name)
                    for name in LEDGER_SECTION_NAMES
                }
                for item in imported_results:
                    records["Phase Results"].append(
                        {
                            "result_id": item["result_id"],
                            "result_kind": "imported-phase-result",
                            "state": item["state"],
                            "record_revision": 1,
                            "data_json": _canonical_json(item),
                        }
                    )
                data = _render_records_ledger(frontmatter, records)
            _inject_failure("document-context-before-ledger-create")
            _write_new_file(ledger_path, data, created_files)
            if _require_regular_nosymlink(ledger_path, "ledger") != data:
                raise ProtocolError(
                    "initialization_verification_failed",
                    "document-only ledger did not reread with its committed bytes",
                )
            return {
                "ok": True,
                "state": "initialized",
                "context": "ledger",
                "created": True,
                "storage_mode": storage_mode,
                "project_id": manifest["project_id"],
                "tree_id": manifest["tree_id"],
                "topic_id": selected_identity["topic_id"],
                "ledger_revision": 1,
                "topic_revision": 1,
                "ledger_path": str(ledger_path),
                "topic_document_path": str(topic_path),
                "imported_result_count": len(imported_results),
                "coordination_state": "unknown",
                "idempotent_replay": False,
            }
        finally:
            fcntl.flock(lock_stream.fileno(), fcntl.LOCK_UN)
            lock_stream.close()
    except ProtocolError:
        _rollback(created_files, created_directories)
        raise
    except OSError as error:
        _rollback(created_files, created_directories)
        raise ProtocolError(
            "initialization_failed",
            "document-only initialization failed",
            cause=str(error),
        ) from error


def _prepare_handoff(request: dict[str, Any]) -> dict[str, Any]:
    project, ledger_path, _, lock_path, owner_ref = _evolution_paths(request)
    _expect_keys(
        request,
        {
            "protocol_version", "operation", "project_path", "project_id", "tree_id",
            "actor_topic_id", "actor_conversation_ref", "expected_ledger_revision",
            "expected_topic_revision", "idempotency_key", "handoff_kind", "target_slug",
            "scope", "work_snapshot", "authoritative_references",
        },
        "prepare-handoff request",
    )
    _validate_uuid4(request["idempotency_key"], "idempotency_key")
    kind = _expect_string(request["handoff_kind"], "handoff_kind", max_bytes=32)
    if kind not in HANDOFF_KINDS:
        raise ProtocolError("invalid_request", "handoff_kind is unsupported")
    target_slug = _expect_string(request["target_slug"], "target_slug", max_bytes=128)
    if not ROOT_SLUG_RE.fullmatch(target_slug):
        raise ProtocolError("invalid_root_slug", "target_slug is invalid")
    scope = _validated_string_list(request["scope"], "scope")
    work_snapshot, snapshot_bytes = _validate_work_snapshot(request["work_snapshot"])
    references, references_digest = _validate_authoritative_references(
        request["authoritative_references"]
    )
    with lock_path.open("a+b") as lock_stream:
        _flock_with_timeout(lock_stream)
        frontmatter, records = _load_records(ledger_path)
        handoff_id = _handoff_id(request["idempotency_key"])
        existing_records = [
            record for record in records["Phase Runs"] if record.get("run_id") == handoff_id
        ]
        if len(existing_records) > 1:
            raise ProtocolError("state_corrupt", "handoff creation identity is duplicated")
        if existing_records:
            existing = _handoff_data(existing_records[0])
            fingerprint = _mutation_fingerprint(request)
            if (
                existing.get("creation_idempotency_key") != request["idempotency_key"]
                or existing.get("creation_fingerprint") != fingerprint
            ):
                raise ProtocolError(
                    "idempotency_conflict",
                    "handoff creation identity was reused with different frozen intent",
                )
            creation_result = json.loads(existing["creation_result_json"])
            creation_result["idempotent_replay"] = True
            creation_result["state"] = existing["state"]
            return creation_result
        replay = _idempotent_result(records, request)
        if replay is not None:
            return replay
        source_topic = _record_by_id(
            records["Current Topics"], "topic_id", request["actor_topic_id"], "topic_id"
        )
        ledger_revision, topic_revision = _validate_revisions(request, frontmatter, source_topic)
        _verify_topic_owner(records, request["actor_topic_id"], owner_ref)
        target_topic_id = (
            request["actor_topic_id"] if kind == "continuation" else f"topic-{uuid.UUID(request['idempotency_key']).hex}"
        )
        attempt_id = _handoff_attempt_id(handoff_id, 1)
        handoff = {
            "handoff_id": handoff_id,
            "record_revision": 1,
            "state": "setup-pending",
            "kind": kind,
            "project_id": request["project_id"],
            "tree_id": request["tree_id"],
            "source_topic_id": request["actor_topic_id"],
            "target_topic_id": target_topic_id,
            "target_slug": target_slug,
            "scope": scope,
            "work_snapshot": work_snapshot,
            "work_snapshot_bytes": snapshot_bytes,
            "authoritative_references": references,
            "authoritative_references_sha256": references_digest,
            "attempts": [],
            "creation_idempotency_key": request["idempotency_key"],
            "creation_fingerprint": _mutation_fingerprint(request),
            "creation_result_json": "",
        }
        identity_envelope, payload_digest, payload_bytes = _handoff_payload(handoff, attempt_id)
        attempt = {
            "attempt_id": attempt_id,
            "attempt_number": 1,
            "state": "setup-pending",
            "binding_eligible": True,
            "payload_sha256": payload_digest,
            "handoff_payload_bytes": payload_bytes,
            "conversation_ref": None,
            "reason": None,
        }
        handoff["attempts"].append(attempt)
        records["Phase Runs"].append(
            {
                "run_id": handoff_id,
                "run_kind": "discussion-handoff",
                "state": "setup-pending",
                "record_revision": 1,
                "data_json": _canonical_json(handoff),
            }
        )
        if kind == "child":
            if any(topic.get("topic_id") == target_topic_id for topic in records["Current Topics"]):
                raise ProtocolError("handoff_identity_conflict", "child topic identity already exists")
            records["Current Topics"].append(
                {
                    "topic_id": target_topic_id,
                    "record_revision": 1,
                    "root_slug": target_slug,
                    "parent_topic_id": request["actor_topic_id"],
                    "current_phase": 0,
                    "phase_state": "setup-pending",
                    "review_state": "unreviewed",
                    "topic_state": "open",
                    "topic_document_path": None,
                }
            )
            records["Relations and Coverage"].append(
                {
                    "relation_id": f"REL-{handoff_id[2:]}",
                    "relation_type": "parent",
                    "source_topic_id": request["actor_topic_id"],
                    "target_topic_id": target_topic_id,
                    "state": "active",
                    "handoff_id": handoff_id,
                }
            )
        else:
            records["Relations and Coverage"].append(
                {
                    "relation_id": f"REL-{handoff_id[2:]}",
                    "relation_type": "continuation",
                    "source_topic_id": request["actor_topic_id"],
                    "target_topic_id": target_topic_id,
                    "state": "setup-pending",
                    "handoff_id": handoff_id,
                }
            )
        next_revision = ledger_revision + 1
        result = {
            **_handoff_result(
                request, handoff, attempt, ledger_revision=next_revision, topic_revision=topic_revision
            ),
            "identity_envelope": identity_envelope,
            "work_snapshot_bytes": snapshot_bytes,
            "handoff_payload_bytes": payload_bytes,
        }
        handoff["creation_result_json"] = _canonical_json(result)
        _store_handoff(
            _record_by_id(records["Phase Runs"], "run_id", handoff_id, "handoff_id"),
            handoff,
        )
        _write_ledger_transaction(
            ledger_path, frontmatter, records, request,
            ledger_revision=next_revision, event_type="handoff-prepared", result=result,
        )
        return result


def _handoff_mutation_context(
    request: dict[str, Any], allowed: set[str]
) -> tuple[Path, Path, Path, str]:
    _, ledger_path, _, lock_path, owner_ref = _evolution_paths(
        request, allow_tree_topic=True
    )
    _expect_keys(
        request,
        {
            "protocol_version", "operation", "project_path", "project_id", "tree_id",
            "actor_topic_id", "actor_conversation_ref", "expected_ledger_revision",
            "expected_topic_revision", "idempotency_key",
        } | allowed,
        f"{request['operation']} request",
    )
    _validate_uuid4(request["idempotency_key"], "idempotency_key")
    return ledger_path, lock_path, Path(request["project_path"]), owner_ref


def _bind_handoff(request: dict[str, Any]) -> dict[str, Any]:
    ledger_path, lock_path, _, owner_ref = _handoff_mutation_context(
        request, {"handoff_id", "attempt_id", "conversation_ref", "verified_identity"}
    )
    conversation_ref = _expect_string(request["conversation_ref"], "conversation_ref", max_bytes=1024)
    verified = request["verified_identity"]
    if not isinstance(verified, dict):
        raise ProtocolError("invalid_request", "verified_identity must be an object")
    _expect_keys(
        verified,
        {"project_id", "tree_id", "topic_id", "handoff_id", "attempt_id", "payload_sha256"},
        "verified_identity",
    )
    with lock_path.open("a+b") as lock_stream:
        _flock_with_timeout(lock_stream)
        frontmatter, records = _load_records(ledger_path)
        replay = _idempotent_result(records, request)
        if replay is not None:
            return replay
        source_topic = _record_by_id(
            records["Current Topics"], "topic_id", request["actor_topic_id"], "topic_id"
        )
        ledger_revision, topic_revision = _validate_revisions(request, frontmatter, source_topic)
        _verify_topic_owner(records, request["actor_topic_id"], owner_ref)
        record = _handoff_record(records, request["handoff_id"])
        handoff = _handoff_data(record)
        if handoff["source_topic_id"] != request["actor_topic_id"]:
            raise ProtocolError("handoff_identity_conflict", "handoff source topic does not match actor")
        attempt = _handoff_attempt(handoff, request["attempt_id"])
        if not attempt["binding_eligible"]:
            raise ProtocolError("handoff_late_arrival", "attempt binding eligibility was revoked")
        if attempt["state"] not in {"setup-pending", "outcome-unknown"}:
            raise ProtocolError("handoff_attempt_state_conflict", "attempt cannot be bound from its current state")
        expected_identity = {
            "project_id": request["project_id"],
            "tree_id": request["tree_id"],
            "topic_id": handoff["target_topic_id"],
            "handoff_id": handoff["handoff_id"],
            "attempt_id": attempt["attempt_id"],
            "payload_sha256": attempt["payload_sha256"],
        }
        if verified != expected_identity:
            raise ProtocolError("handoff_identity_conflict", "verified handoff identity does not match frozen intent")
        active = [
            binding for binding in records["Conversation Bindings"]
            if binding.get("topic_id") == handoff["target_topic_id"] and binding.get("binding_state") == "active"
        ]
        superseded = None
        if handoff["kind"] == "continuation":
            if len(active) != 1 or active[0].get("conversation_ref") != owner_ref:
                raise ProtocolError("handoff_attempt_state_conflict", "continuation source binding changed")
            superseded = active[0]["conversation_ref"]
            if conversation_ref == superseded:
                raise ProtocolError(
                    "handoff_identity_conflict",
                    "continuation must bind a new conversation reference",
                )
            prior_handoff_id = active[0].get("handoff_id")
            prior_attempt_id = active[0].get("attempt_id")
            if prior_handoff_id is not None or prior_attempt_id is not None:
                if not isinstance(prior_handoff_id, str) or not isinstance(prior_attempt_id, str):
                    raise ProtocolError("state_corrupt", "active continuation binding provenance is incomplete")
                prior_record = _handoff_record(records, prior_handoff_id)
                prior_handoff = _handoff_data(prior_record)
                prior_attempt = _handoff_attempt(prior_handoff, prior_attempt_id)
                if (
                    prior_handoff["target_topic_id"] != handoff["target_topic_id"]
                    or prior_attempt.get("conversation_ref") != superseded
                    or prior_attempt["state"] not in {
                        "bound-pending-acceptance", "accepted-awaiting-next-turn", "active"
                    }
                ):
                    raise ProtocolError("state_corrupt", "active continuation binding provenance is invalid")
                prior_attempt["state"] = "superseded"
                prior_handoff["state"] = "superseded"
                prior_handoff["record_revision"] += 1
                _store_handoff(prior_record, prior_handoff)
            active[0]["binding_state"] = "superseded"
            active[0]["superseded_by"] = conversation_ref
            relation = _record_by_id(
                records["Relations and Coverage"], "handoff_id", handoff["handoff_id"], "continuation relation"
            )
            relation["state"] = "active"
            relation["continuation_of"] = superseded
        elif active:
            raise ProtocolError("handoff_attempt_state_conflict", "child topic already has an active binding")
        records["Conversation Bindings"].append(
            {
                "topic_id": handoff["target_topic_id"],
                "conversation_ref": conversation_ref,
                "binding_state": "active",
                "record_revision": 1,
                "handoff_id": handoff["handoff_id"],
                "attempt_id": attempt["attempt_id"],
            }
        )
        attempt.update({"state": "bound-pending-acceptance", "conversation_ref": conversation_ref})
        handoff["state"] = "bound-pending-acceptance"
        handoff["record_revision"] += 1
        _store_handoff(record, handoff)
        next_revision = ledger_revision + 1
        result = {
            **_handoff_result(request, handoff, attempt, ledger_revision=next_revision, topic_revision=topic_revision),
            "conversation_ref": conversation_ref,
            "active_conversation_ref": conversation_ref,
            "superseded_conversation_ref": superseded,
        }
        _inject_failure("handoff-before-binding-ledger-write")
        _write_ledger_transaction(
            ledger_path, frontmatter, records, request,
            ledger_revision=next_revision, event_type="handoff-bound", result=result,
        )
        return result


def _accept_handoff(request: dict[str, Any]) -> dict[str, Any]:
    ledger_path, lock_path, _, owner_ref = _handoff_mutation_context(
        request,
        {"handoff_id", "attempt_id", "payload_sha256", "source_reference_sha256", "turn_number"},
    )
    if request["turn_number"] != 1:
        raise ProtocolError("handoff_identity_conflict", "handoff acceptance must occur in the first turn")
    with lock_path.open("a+b") as lock_stream:
        _flock_with_timeout(lock_stream)
        frontmatter, records = _load_records(ledger_path)
        replay = _idempotent_result(records, request)
        if replay is not None:
            return replay
        topic_record = _record_by_id(
            records["Current Topics"], "topic_id", request["actor_topic_id"], "topic_id"
        )
        ledger_revision, topic_revision = _validate_revisions(request, frontmatter, topic_record)
        _verify_topic_owner(records, request["actor_topic_id"], owner_ref)
        record = _handoff_record(records, request["handoff_id"])
        handoff = _handoff_data(record)
        attempt = _handoff_attempt(handoff, request["attempt_id"])
        if handoff["target_topic_id"] != request["actor_topic_id"] or attempt.get("conversation_ref") != owner_ref:
            raise ProtocolError("handoff_identity_conflict", "accepting conversation does not own the target topic")
        if attempt["state"] != "bound-pending-acceptance":
            raise ProtocolError("handoff_attempt_state_conflict", "attempt is not awaiting acceptance")
        if (
            request["payload_sha256"] != attempt["payload_sha256"]
            or request["source_reference_sha256"] != handoff["authoritative_references_sha256"]
        ):
            raise ProtocolError("handoff_identity_conflict", "handoff payload or source digest verification failed")
        attempt.update({"state": "accepted-awaiting-next-turn", "accepted_turn": 1})
        handoff["state"] = "accepted-awaiting-next-turn"
        handoff["record_revision"] += 1
        _store_handoff(record, handoff)
        next_revision = ledger_revision + 1
        result = {
            **_handoff_result(request, handoff, attempt, ledger_revision=next_revision, topic_revision=topic_revision),
            "substantive_discussion_allowed": False,
        }
        _write_ledger_transaction(
            ledger_path, frontmatter, records, request,
            ledger_revision=next_revision, event_type="handoff-accepted", result=result,
        )
        return result


def _authorize_handoff_discussion(request: dict[str, Any]) -> dict[str, Any]:
    ledger_path, lock_path, _, owner_ref = _handoff_mutation_context(
        request, {"handoff_id", "attempt_id", "turn_number"}
    )
    turn_number = request["turn_number"]
    if not isinstance(turn_number, int) or isinstance(turn_number, bool):
        raise ProtocolError("invalid_request", "turn_number must be an integer")
    with lock_path.open("a+b") as lock_stream:
        _flock_with_timeout(lock_stream)
        frontmatter, records = _load_records(ledger_path)
        replay = _idempotent_result(records, request)
        if replay is not None:
            return replay
        topic_record = _record_by_id(records["Current Topics"], "topic_id", request["actor_topic_id"], "topic_id")
        ledger_revision, topic_revision = _validate_revisions(request, frontmatter, topic_record)
        _verify_topic_owner(records, request["actor_topic_id"], owner_ref)
        record = _handoff_record(records, request["handoff_id"])
        handoff = _handoff_data(record)
        attempt = _handoff_attempt(handoff, request["attempt_id"])
        if attempt["state"] != "accepted-awaiting-next-turn":
            raise ProtocolError("handoff_attempt_state_conflict", "attempt is not awaiting a later turn")
        if handoff["target_topic_id"] != request["actor_topic_id"] or attempt.get("conversation_ref") != owner_ref:
            raise ProtocolError("handoff_identity_conflict", "discussion authorization actor is not the accepted target")
        if turn_number <= attempt["accepted_turn"]:
            raise ProtocolError("handoff_next_turn_required", "substantive discussion requires a later turn")
        attempt["state"] = "active"
        handoff["state"] = "active"
        handoff["record_revision"] += 1
        _store_handoff(record, handoff)
        next_revision = ledger_revision + 1
        result = {
            **_handoff_result(request, handoff, attempt, ledger_revision=next_revision, topic_revision=topic_revision),
            "substantive_discussion_allowed": True,
        }
        _write_ledger_transaction(
            ledger_path, frontmatter, records, request,
            ledger_revision=next_revision, event_type="handoff-discussion-authorized", result=result,
        )
        return result


def _transition_handoff_attempt(
    request: dict[str, Any], *, target_state: str, event_type: str
) -> dict[str, Any]:
    extra = {"handoff_id", "attempt_id"}
    if target_state in {"failed", "cancelled"}:
        extra.add("reason")
    ledger_path, lock_path, _, owner_ref = _handoff_mutation_context(request, extra)
    reason = (
        _expect_string(request["reason"], "reason", max_bytes=2048)
        if "reason" in extra
        else None
    )
    with lock_path.open("a+b") as lock_stream:
        _flock_with_timeout(lock_stream)
        frontmatter, records = _load_records(ledger_path)
        replay = _idempotent_result(records, request)
        if replay is not None:
            return replay
        topic_record = _record_by_id(records["Current Topics"], "topic_id", request["actor_topic_id"], "topic_id")
        ledger_revision, topic_revision = _validate_revisions(request, frontmatter, topic_record)
        _verify_topic_owner(records, request["actor_topic_id"], owner_ref)
        record = _handoff_record(records, request["handoff_id"])
        handoff = _handoff_data(record)
        if handoff["source_topic_id"] != request["actor_topic_id"]:
            raise ProtocolError("handoff_identity_conflict", "only the source topic may record creation outcomes")
        attempt = _handoff_attempt(handoff, request["attempt_id"])
        allowed_states = {
            "outcome-unknown": {"setup-pending"},
            "failed": {"setup-pending", "outcome-unknown"},
            "cancelled": {"setup-pending", "outcome-unknown", "failed"},
        }[target_state]
        if attempt["state"] not in allowed_states:
            raise ProtocolError("handoff_attempt_state_conflict", "attempt cannot enter requested state")
        attempt.update({"state": target_state, "reason": reason})
        if target_state in {"failed", "cancelled"}:
            attempt["binding_eligible"] = False
        handoff["state"] = target_state
        handoff["record_revision"] += 1
        _store_handoff(record, handoff)
        next_revision = ledger_revision + 1
        result = {
            **_handoff_result(request, handoff, attempt, ledger_revision=next_revision, topic_revision=topic_revision),
            "binding_eligible": attempt["binding_eligible"],
        }
        _write_ledger_transaction(
            ledger_path, frontmatter, records, request,
            ledger_revision=next_revision, event_type=event_type, result=result,
        )
        return result


def _retry_handoff(request: dict[str, Any]) -> dict[str, Any]:
    allowed = {"handoff_id", "prior_attempt_id", "forced"}
    if request.get("forced") is True:
        allowed.add("user_authorization")
    ledger_path, lock_path, _, owner_ref = _handoff_mutation_context(request, allowed)
    forced = request["forced"]
    if not isinstance(forced, bool):
        raise ProtocolError("invalid_request", "forced must be boolean")
    if forced:
        _expect_string(request["user_authorization"], "user_authorization", max_bytes=2048)
    with lock_path.open("a+b") as lock_stream:
        _flock_with_timeout(lock_stream)
        frontmatter, records = _load_records(ledger_path)
        replay = _idempotent_result(records, request)
        if replay is not None:
            return replay
        topic_record = _record_by_id(records["Current Topics"], "topic_id", request["actor_topic_id"], "topic_id")
        ledger_revision, topic_revision = _validate_revisions(request, frontmatter, topic_record)
        _verify_topic_owner(records, request["actor_topic_id"], owner_ref)
        record = _handoff_record(records, request["handoff_id"])
        handoff = _handoff_data(record)
        if handoff["source_topic_id"] != request["actor_topic_id"]:
            raise ProtocolError("handoff_identity_conflict", "only the source topic may retry creation")
        prior = _handoff_attempt(handoff, request["prior_attempt_id"])
        if prior is not handoff["attempts"][-1]:
            raise ProtocolError("handoff_attempt_state_conflict", "only the latest attempt may be retried")
        if prior["state"] == "outcome-unknown" and not forced:
            raise ProtocolError("handoff_reconciliation_required", "outcome-unknown attempt must be reconciled first")
        if prior["state"] not in {"failed", "cancelled", "outcome-unknown"}:
            raise ProtocolError("handoff_attempt_state_conflict", "attempt is not terminal or uncertain")
        if not forced and prior["state"] != "failed":
            raise ProtocolError("handoff_reconciliation_required", "retry requires explicit failure or user authorization")
        prior["binding_eligible"] = False
        number = len(handoff["attempts"]) + 1
        attempt_id = _handoff_attempt_id(handoff["handoff_id"], number)
        _, payload_digest, payload_bytes = _handoff_payload(handoff, attempt_id)
        attempt = {
            "attempt_id": attempt_id,
            "attempt_number": number,
            "state": "setup-pending",
            "binding_eligible": True,
            "payload_sha256": payload_digest,
            "handoff_payload_bytes": payload_bytes,
            "conversation_ref": None,
            "reason": None,
        }
        handoff["attempts"].append(attempt)
        handoff["state"] = "setup-pending"
        handoff["record_revision"] += 1
        _store_handoff(record, handoff)
        next_revision = ledger_revision + 1
        result = _handoff_result(
            request, handoff, attempt, ledger_revision=next_revision, topic_revision=topic_revision
        )
        _write_ledger_transaction(
            ledger_path, frontmatter, records, request,
            ledger_revision=next_revision, event_type="handoff-retried", result=result,
        )
        return result


def _reconcile_handoff_attempt(request: dict[str, Any]) -> dict[str, Any]:
    allowed = {"handoff_id", "attempt_id", "outcome"}
    if request.get("outcome") == "not-created":
        allowed.add("reason")
    ledger_path, lock_path, _, owner_ref = _handoff_mutation_context(request, allowed)
    outcome = _expect_string(request["outcome"], "outcome", max_bytes=32)
    if outcome not in {"still-unknown", "not-created"}:
        raise ProtocolError("invalid_request", "reconciliation outcome is unsupported")
    reason = _expect_string(request["reason"], "reason", max_bytes=2048) if outcome == "not-created" else None
    with lock_path.open("a+b") as lock_stream:
        _flock_with_timeout(lock_stream)
        frontmatter, records = _load_records(ledger_path)
        replay = _idempotent_result(records, request)
        if replay is not None:
            return replay
        topic_record = _record_by_id(records["Current Topics"], "topic_id", request["actor_topic_id"], "topic_id")
        ledger_revision, topic_revision = _validate_revisions(request, frontmatter, topic_record)
        _verify_topic_owner(records, request["actor_topic_id"], owner_ref)
        record = _handoff_record(records, request["handoff_id"])
        handoff = _handoff_data(record)
        attempt = _handoff_attempt(handoff, request["attempt_id"])
        if handoff["source_topic_id"] != request["actor_topic_id"] or attempt["state"] != "outcome-unknown":
            raise ProtocolError("handoff_attempt_state_conflict", "attempt is not reconcilable by this topic")
        if outcome == "not-created":
            attempt.update({"state": "failed", "binding_eligible": False, "reason": reason})
            handoff["state"] = "failed"
        handoff["record_revision"] += 1
        _store_handoff(record, handoff)
        next_revision = ledger_revision + 1
        result = {
            **_handoff_result(request, handoff, attempt, ledger_revision=next_revision, topic_revision=topic_revision),
            "reconciliation_outcome": outcome,
            "binding_eligible": attempt["binding_eligible"],
        }
        _write_ledger_transaction(
            ledger_path, frontmatter, records, request,
            ledger_revision=next_revision, event_type="handoff-reconciled", result=result,
        )
        return result


def _submit_child_result(request: dict[str, Any]) -> dict[str, Any]:
    ledger_path, lock_path, _, owner_ref = _handoff_mutation_context(
        request, {"handoff_id", "attempt_id", "result_scope", "summary"}
    )
    result_scope = _validated_string_list(request["result_scope"], "result_scope")
    summary = _expect_string(request["summary"], "summary", max_bytes=4096)
    with lock_path.open("a+b") as lock_stream:
        _flock_with_timeout(lock_stream)
        frontmatter, records = _load_records(ledger_path)
        replay = _idempotent_result(records, request)
        if replay is not None:
            return replay
        topic_record = _record_by_id(records["Current Topics"], "topic_id", request["actor_topic_id"], "topic_id")
        ledger_revision, topic_revision = _validate_revisions(request, frontmatter, topic_record)
        _verify_topic_owner(records, request["actor_topic_id"], owner_ref)
        record = _handoff_record(records, request["handoff_id"])
        handoff = _handoff_data(record)
        if (
            handoff["kind"] != "child"
            or handoff["target_topic_id"] != request["actor_topic_id"]
        ):
            raise ProtocolError(
                "child_result_state_conflict",
                "result does not originate from this child topic",
            )
        active_binding = [
            binding for binding in records["Conversation Bindings"]
            if binding.get("topic_id") == request["actor_topic_id"]
            and binding.get("conversation_ref") == owner_ref
            and binding.get("binding_state") == "active"
        ]
        if len(active_binding) != 1 or active_binding[0].get("attempt_id") != request["attempt_id"]:
            raise ProtocolError(
                "child_result_state_conflict",
                "result attempt is not the current active child binding",
            )
        provenance_handoff = _handoff_data(
            _handoff_record(records, active_binding[0].get("handoff_id"))
        )
        attempt = _handoff_attempt(provenance_handoff, request["attempt_id"])
        if (
            provenance_handoff["target_topic_id"] != request["actor_topic_id"]
            or attempt["state"] != "active"
            or attempt.get("conversation_ref") != owner_ref
        ):
            raise ProtocolError(
                "child_result_state_conflict",
                "only the active accepted child attempt may submit a result",
            )
        seed = uuid.UUID(request["idempotency_key"]).hex
        child_result_id = f"CR-{seed}"
        next_revision = ledger_revision + 1
        claim = {
            "result_id": child_result_id,
            "result_kind": "child-topic-result",
            "handoff_id": handoff["handoff_id"],
            "attempt_id": attempt["attempt_id"],
            "provenance_handoff_id": provenance_handoff["handoff_id"],
            "source_topic_id": handoff["target_topic_id"],
            "target_topic_id": handoff["source_topic_id"],
            "result_scope_json": _canonical_json(result_scope),
            "summary": summary,
            "state": "pending",
            "record_revision": 1,
        }
        records["Phase Results"].append(claim)
        result = {
            "ok": True, "state": "pending-parent-acceptance", "idempotent_replay": False,
            "project_id": request["project_id"], "tree_id": request["tree_id"],
            "topic_id": request["actor_topic_id"], "ledger_revision": next_revision,
            "record_revision": topic_revision, "handoff_id": handoff["handoff_id"],
            "attempt_id": attempt["attempt_id"], "child_result_id": child_result_id,
        }
        _write_ledger_transaction(
            ledger_path, frontmatter, records, request,
            ledger_revision=next_revision, event_type="child-result-submitted", result=result,
        )
        return result


def _record_child_result(request: dict[str, Any]) -> dict[str, Any]:
    ledger_path, lock_path, _, owner_ref = _handoff_mutation_context(
        request, {"handoff_id", "child_result_id", "effect"}
    )
    effect = _expect_string(request["effect"], "effect", max_bytes=32)
    if effect not in {"absorb", "impact"}:
        raise ProtocolError("invalid_request", "effect must be absorb or impact")
    with lock_path.open("a+b") as lock_stream:
        _flock_with_timeout(lock_stream)
        frontmatter, records = _load_records(ledger_path)
        replay = _idempotent_result(records, request)
        if replay is not None:
            return replay
        topic_record = _record_by_id(records["Current Topics"], "topic_id", request["actor_topic_id"], "topic_id")
        ledger_revision, topic_revision = _validate_revisions(request, frontmatter, topic_record)
        _verify_topic_owner(records, request["actor_topic_id"], owner_ref)
        record = _handoff_record(records, request["handoff_id"])
        handoff = _handoff_data(record)
        if handoff["kind"] != "child" or handoff["source_topic_id"] != request["actor_topic_id"]:
            raise ProtocolError("handoff_identity_conflict", "result is not owned by this child handoff parent")
        claim = _record_by_id(
            records["Phase Results"], "result_id",
            _expect_string(request["child_result_id"], "child_result_id", max_bytes=64),
            "child_result_id",
        )
        if (
            claim.get("result_kind") != "child-topic-result"
            or claim.get("handoff_id") != handoff["handoff_id"]
            or claim.get("source_topic_id") != handoff["target_topic_id"]
            or claim.get("target_topic_id") != request["actor_topic_id"]
            or claim.get("state") != "pending"
        ):
            raise ProtocolError("child_result_state_conflict", "child result claim is not pending for this parent")
        result_scope = _json_field(claim, "result_scope_json", "child result")
        summary = claim["summary"]
        within_scope = set(result_scope).issubset(set(handoff["scope"]))
        if effect == "absorb" and not within_scope:
            raise ProtocolError("impact_state_conflict", "cross-topic result cannot be silently absorbed")
        seed = uuid.UUID(request["idempotency_key"]).hex
        next_revision = ledger_revision + 1
        if effect == "absorb":
            relation_id = f"REL-{seed}"
            records["Relations and Coverage"].append(
                {
                    "relation_id": relation_id,
                    "relation_type": "absorbs",
                    "source_topic_id": request["actor_topic_id"],
                    "target_topic_id": handoff["target_topic_id"],
                    "scope_json": _canonical_json(result_scope),
                    "summary": summary,
                    "state": "active",
                    "handoff_id": handoff["handoff_id"],
                }
            )
            result = {
                "ok": True, "state": "absorbed", "idempotent_replay": False,
                "project_id": request["project_id"], "tree_id": request["tree_id"],
                "topic_id": request["actor_topic_id"], "ledger_revision": next_revision,
                "record_revision": topic_revision, "handoff_id": handoff["handoff_id"],
                "relation_id": relation_id,
            }
            claim["state"] = "absorbed"
        else:
            impact_id = f"IMP-{seed}"
            impact = {
                "impact_id": impact_id,
                "source_topic_id": handoff["target_topic_id"],
                "target_topic_id": request["actor_topic_id"],
                "scope": result_scope,
                "summary": summary,
                "state": "pending",
                "handoff_id": handoff["handoff_id"],
            }
            records["Impacts"].append(
                {"impact_id": impact_id, "topic_id": request["actor_topic_id"], "data_json": _canonical_json(impact)}
            )
            result = {
                "ok": True, "state": "pending-impact", "idempotent_replay": False,
                "project_id": request["project_id"], "tree_id": request["tree_id"],
                "topic_id": request["actor_topic_id"], "ledger_revision": next_revision,
                "record_revision": topic_revision, "handoff_id": handoff["handoff_id"],
                "impact_id": impact_id,
            }
            claim["state"] = "impact-recorded"
        claim["record_revision"] += 1
        _write_ledger_transaction(
            ledger_path, frontmatter, records, request,
            ledger_revision=next_revision, event_type=f"child-result-{result['state']}", result=result,
        )
        return result


def _read_handoff(request: dict[str, Any]) -> dict[str, Any]:
    _, ledger_path, _, lock_path, owner_ref = _evolution_paths(
        request, query=True, allow_tree_topic=True
    )
    _expect_keys(
        request,
        {"protocol_version", "operation", "project_path", "project_id", "tree_id", "actor_topic_id", "actor_conversation_ref", "handoff_id"},
        "read-handoff request",
    )
    with lock_path.open("a+b") as lock_stream:
        _flock_with_timeout(lock_stream)
        frontmatter, records = _load_records(ledger_path)
        handoff = _handoff_data(_handoff_record(records, request["handoff_id"]))
        if request["actor_topic_id"] not in {handoff["source_topic_id"], handoff["target_topic_id"]}:
            raise ProtocolError("handoff_identity_conflict", "topic is outside this handoff")
        authorized_bindings = [
            binding for binding in records["Conversation Bindings"]
            if binding.get("topic_id") == request["actor_topic_id"]
            and binding.get("conversation_ref") == owner_ref
            and binding.get("binding_state") in {"active", "superseded"}
        ]
        if len(authorized_bindings) != 1:
            raise ProtocolError(
                "document_ownership_conflict",
                "the caller is not a verified participant in this handoff topic",
            )
        target_topic = _record_by_id(
            records["Current Topics"], "topic_id", handoff["target_topic_id"], "target_topic_id"
        )
        bindings = [
            dict(binding) for binding in records["Conversation Bindings"]
            if binding.get("topic_id") == handoff["target_topic_id"]
        ]
        return {
            "ok": True,
            "state": "read",
            "ledger_revision": int(frontmatter["ledger_revision"]),
            "record_revision": target_topic["record_revision"],
            "handoff": {key: value for key, value in handoff.items() if key != "attempts"},
            "attempts": [dict(attempt) for attempt in handoff["attempts"]],
            "target_topic": dict(target_topic),
            "bindings": bindings,
        }


def _validate_checkpoints(
    project: Path,
    records: dict[str, list[dict[str, Any]]],
) -> list[dict[str, Any]]:
    checkpoints = []
    for record in records["Checkpoints"]:
        checkpoint = _checkpoint_data(record)
        try:
            creation_key = str(uuid.UUID(checkpoint["creation_idempotency_key"]))
        except (KeyError, TypeError, ValueError, AttributeError) as error:
            raise ProtocolError(
                "state_corrupt", "checkpoint creation identity is invalid"
            ) from error
        if (
            record.get("checkpoint_id") != checkpoint.get("checkpoint_id")
            or record.get("state") != checkpoint.get("state")
            or record.get("record_revision") != checkpoint.get("record_revision")
            or checkpoint["checkpoint_id"] != _checkpoint_id(creation_key)
            or not isinstance(checkpoint.get("creation_fingerprint"), str)
            or not isinstance(checkpoint.get("creation_result_json"), str)
        ):
            raise ProtocolError("state_corrupt", "checkpoint record envelope does not match its data")
        paths = json.loads(checkpoint["paths_json"])
        digests = json.loads(checkpoint["document_digests_json"])
        if (
            not isinstance(paths, list)
            or not paths
            or sorted(paths) != paths
            or set(paths) != set(digests)
            or checkpoint["path_set_digest"] != _sha256(_canonical_json(paths).encode("utf-8"))
        ):
            raise ProtocolError("state_corrupt", "checkpoint frozen paths or digests are invalid")
        if checkpoint["state"] == "completed":
            if checkpoint["storage_kind"] == "git":
                expected_parent = checkpoint.get("replacement_parent")
                if checkpoint.get("checkpoint_ref"):
                    ref_commit = _git(project, ["rev-parse", "--verify", checkpoint["checkpoint_ref"]]).decode("ascii").strip()
                    if ref_commit != checkpoint["published_identity"]:
                        raise ProtocolError("checkpoint_history_mismatch", "checkpoint ref does not resolve to published identity")
                if _commit_matches_checkpoint(project, checkpoint["published_identity"], checkpoint, expected_parent=expected_parent) is None:
                    raise ProtocolError("checkpoint_history_mismatch", "completed Git checkpoint no longer fully verifies")
            else:
                snapshot_path = Path(checkpoint["snapshot_path"])
                snapshot = _require_regular_nosymlink(snapshot_path, "checkpoint snapshot")
                if _sha256(snapshot) != checkpoint["published_identity"]:
                    raise ProtocolError("checkpoint_snapshot_corrupt", "completed snapshot digest no longer verifies")
        checkpoints.append(dict(checkpoint))
    return sorted(checkpoints, key=lambda item: item["checkpoint_id"])


def _validate_handoffs(records: dict[str, list[dict[str, Any]]]) -> int:
    handoffs = [
        record for record in records["Phase Runs"]
        if record.get("run_kind") == "discussion-handoff"
    ]
    active_by_topic: dict[str, int] = {}
    for binding in records["Conversation Bindings"]:
        if binding.get("binding_state") == "active":
            topic_id = binding.get("topic_id")
            active_by_topic[topic_id] = active_by_topic.get(topic_id, 0) + 1
    if any(count != 1 for count in active_by_topic.values()):
        raise ProtocolError("state_corrupt", "a topic has multiple active conversation bindings")
    for record in handoffs:
        handoff = _handoff_data(record)
        if (
            handoff.get("kind") not in HANDOFF_KINDS
            or handoff.get("project_id") is None
            or handoff.get("tree_id") is None
            or handoff.get("authoritative_references_sha256")
            != _sha256(_canonical_json(handoff.get("authoritative_references")).encode("utf-8"))
        ):
            raise ProtocolError("state_corrupt", "handoff frozen identity or references are invalid")
        attempts = handoff.get("attempts")
        if not isinstance(attempts, list) or not attempts:
            raise ProtocolError("state_corrupt", "handoff attempt history is empty")
        eligible = 0
        for number, attempt in enumerate(attempts, start=1):
            if (
                attempt.get("attempt_number") != number
                or attempt.get("attempt_id") != _handoff_attempt_id(handoff["handoff_id"], number)
            ):
                raise ProtocolError("state_corrupt", "handoff attempt history is not unique and ordered")
            _, payload_digest, payload_bytes = _handoff_payload(handoff, attempt["attempt_id"])
            if (
                attempt.get("payload_sha256") != payload_digest
                or attempt.get("handoff_payload_bytes") != payload_bytes
            ):
                raise ProtocolError("state_corrupt", "handoff attempt payload digest is invalid")
            if attempt.get("binding_eligible") is True:
                eligible += 1
        if eligible > 1:
            raise ProtocolError("state_corrupt", "handoff has multiple binding-eligible attempts")
        bound_attempts = [attempt for attempt in attempts if attempt.get("conversation_ref")]
        for bound_attempt in bound_attempts:
            attempt_bindings = [
                binding for binding in records["Conversation Bindings"]
                if binding.get("topic_id") == handoff["target_topic_id"]
                and binding.get("handoff_id") == handoff["handoff_id"]
                and binding.get("attempt_id") == bound_attempt["attempt_id"]
                and binding.get("conversation_ref") == bound_attempt["conversation_ref"]
                and binding.get("binding_state") in {"active", "superseded"}
            ]
            if len(attempt_bindings) != 1:
                raise ProtocolError(
                    "state_corrupt", "bound handoff attempt does not have one traceable binding"
                )
        if handoff["state"] in {"bound-pending-acceptance", "accepted-awaiting-next-turn", "active"}:
            current = bound_attempts[-1] if bound_attempts else None
            current_bindings = [
                binding for binding in records["Conversation Bindings"]
                if current is not None
                and binding.get("topic_id") == handoff["target_topic_id"]
                and binding.get("handoff_id") == handoff["handoff_id"]
                and binding.get("attempt_id") == current["attempt_id"]
                and binding.get("binding_state") == "active"
            ]
            if len(current_bindings) != 1:
                raise ProtocolError("state_corrupt", "current handoff does not own its active binding")
    return len(handoffs)


def _inspect_archive_checkpoint(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ProtocolError("archive_checkpoint_invalid", "closure_checkpoint must be an object")
    _expect_keys(
        value,
        {"file_bytes", "file_sha256", "path", "version"},
        "closure_checkpoint",
    )
    path = Path(_expect_string(value["path"], "closure_checkpoint.path", max_bytes=4096))
    if not path.is_absolute():
        raise ProtocolError("archive_checkpoint_invalid", "closure checkpoint path must be absolute")
    report = _run_supervision_cli(
        ["inspect-closure-checkpoint", "--checkpoint", str(path)],
        "archive_checkpoint_invalid",
    )
    if (
        report.get("phase") != "complete"
        or report.get("closure_version") != value["version"]
        or report.get("file_bytes") != value["file_bytes"]
        or report.get("file_sha256") != value["file_sha256"]
    ):
        raise ProtocolError(
            "archive_checkpoint_invalid",
            "closure checkpoint is not the exact completed retained receipt",
        )
    return report


def _record_archive_complete(request: dict[str, Any]) -> dict[str, Any]:
    project, ledger_path, _, lock_path, owner_ref = _evolution_paths(
        request, allow_tree_topic=True
    )
    _expect_keys(
        request,
        {
            "protocol_version", "operation", "project_path", "project_id", "tree_id",
            "actor_topic_id", "actor_conversation_ref", "expected_ledger_revision",
            "expected_topic_revision", "idempotency_key", "implementation_id",
            "effective_phase_result_id", "source_checkpoint_id", "source_identity",
            "execution_mode", "implementation_record_revision", "merge_commit",
            "implementation_commit", "documentation_proposals", "closure_checkpoint",
            "source_host_id", "source_task_id",
        },
        "record-archive-complete request",
    )
    _validate_uuid4(request["idempotency_key"], "idempotency_key")
    execution_mode = _expect_string(request["execution_mode"], "execution_mode")
    expected_closure_version = {
        "exclusive-checkout-v2": 2,
        "isolated-worktree-v1": 3,
    }.get(execution_mode)
    if expected_closure_version is None:
        raise ProtocolError("archive_authority_invalid", "execution mode has no archive protocol")
    proposals = request["documentation_proposals"]
    if not isinstance(proposals, list):
        raise ProtocolError("invalid_request", "documentation_proposals must be an array")
    normalized_proposals = []
    for index, proposal in enumerate(proposals):
        if not isinstance(proposal, dict):
            raise ProtocolError("invalid_request", f"documentation_proposals[{index}] must be an object")
        _expect_keys(
            proposal,
            {"base_sha256", "outcome", "path", "proposal_sha256"},
            f"documentation_proposals[{index}]",
        )
        outcome = _expect_string(proposal["outcome"], f"documentation_proposals[{index}].outcome")
        if outcome not in {"applied", "no-op"}:
            raise ProtocolError(
                "archive_authority_invalid",
                "conflicted or unverified documentation proposals block archive completion",
            )
        normalized_proposals.append(
            {
                "base_sha256": _expect_string(
                    proposal["base_sha256"], f"documentation_proposals[{index}].base_sha256", max_bytes=64
                ),
                "outcome": outcome,
                "path": _expect_string(proposal["path"], f"documentation_proposals[{index}].path", max_bytes=4096),
                "proposal_sha256": _expect_string(
                    proposal["proposal_sha256"], f"documentation_proposals[{index}].proposal_sha256", max_bytes=64
                ),
            }
        )
        if not SHA256_RE.fullmatch(normalized_proposals[-1]["base_sha256"]) or not SHA256_RE.fullmatch(normalized_proposals[-1]["proposal_sha256"]):
            raise ProtocolError("invalid_request", "documentation proposal digests must be SHA-256")

    # Supervision and Git authority are read without the discussion lock.
    closure = _inspect_archive_checkpoint(request["closure_checkpoint"])
    if closure.get("closure_version") != expected_closure_version:
        raise ProtocolError("archive_checkpoint_invalid", "closure protocol does not match execution mode")
    merge_commit = _expect_string(request["merge_commit"], "merge_commit", max_bytes=128)
    implementation_commit = _expect_string(
        request["implementation_commit"], "implementation_commit", max_bytes=128
    )
    source_host_id = _expect_string(request["source_host_id"], "source_host_id", max_bytes=256)
    source_task_id = _expect_string(request["source_task_id"], "source_task_id", max_bytes=256)
    ancestry = subprocess.run(
        ["git", "merge-base", "--is-ancestor", merge_commit, "HEAD"],
        cwd=project,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    if ancestry.returncode != 0:
        raise ProtocolError(
            "archive_authority_invalid",
            "merge commit is not an ancestor of the verified base checkout",
            cause=ancestry.stderr.strip() or None,
        )
    implementation_ancestry = subprocess.run(
        ["git", "merge-base", "--is-ancestor", implementation_commit, merge_commit],
        cwd=project,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    if implementation_ancestry.returncode != 0:
        raise ProtocolError(
            "archive_authority_invalid",
            "implementation commit is not an ancestor of the merge commit",
            cause=implementation_ancestry.stderr.strip() or None,
        )

    with lock_path.open("a+b") as lock_stream:
        _flock_with_timeout(lock_stream)
        frontmatter, records = _load_records(ledger_path)
        replay = _idempotent_result(records, request)
        if replay is not None:
            return replay
        topic = _record_by_id(records["Current Topics"], "topic_id", request["actor_topic_id"], "topic_id")
        ledger_revision, topic_revision = _validate_revisions(request, frontmatter, topic)
        _verify_topic_owner(records, request["actor_topic_id"], owner_ref)
        implementation_record, implementation = _implementation_authority(records, request)
        execution = implementation.get("execution")
        if (
            not isinstance(execution, dict)
            or execution.get("mode") != execution_mode
            or execution.get("source_checkpoint_id") != request["source_checkpoint_id"]
            or execution.get("source_identity") != request["source_identity"]
            or execution.get("source_host_id") != source_host_id
            or execution.get("source_task_id") != source_task_id
            or implementation.get("record_revision") != request["implementation_record_revision"]
        ):
            raise ProtocolError("archive_authority_invalid", "implementation source chain or mode changed")
        checkpoint = _verified_implementation_source(
            records, request, request["source_checkpoint_id"], request["source_identity"]
        )
        phase_result_record = _record_by_id(
            records["Phase Results"], "result_id", request["effective_phase_result_id"], "effective_phase_result_id"
        )
        phase_result = _json_field(phase_result_record, "data_json", "phase result")
        phase_run_id = execution.get("phase_run_id")
        phase_run_record = _record_by_id(
            records["Phase Runs"], "run_id", phase_run_id, "phase_run_id"
        )
        phase_run = _phase_data(phase_run_record)
        if (
            phase_result_record.get("state") != "completed"
            or phase_result.get("topic_id") != request["actor_topic_id"]
            or phase_result.get("to_phase") != 3
            or phase_result.get("phase_run_id") != phase_run_id
            or phase_run_record.get("state") != "completed"
            or phase_run.get("source_topic_id") != request["actor_topic_id"]
            or phase_run.get("to_phase") != 3
        ):
            raise ProtocolError("archive_authority_invalid", "effective phase-3 result is not completed for this source topic")
        closure_facts = closure.get("facts", {})
        scope = implementation.get("scope") or {}
        discussion_binding = {
            "effective_phase_result_id": phase_result["result_id"],
            "execution_sha256": _sha256(_canonical_json(execution).encode("utf-8")),
            "implementation_id": implementation["implementation_id"],
            "implementation_record_revision": implementation["record_revision"],
            "phase_run_id": phase_run_id,
            "project_id": request["project_id"],
            "scope_sha256": _sha256(_canonical_json(scope).encode("utf-8")),
            "source_checkpoint_id": checkpoint["checkpoint_id"],
            "source_identity": checkpoint["published_identity"],
            "topic_id": request["actor_topic_id"],
            "tree_id": request["tree_id"],
        }
        if (
            closure_facts.get("repository") != str(project)
            or closure_facts.get("checkout_path") != str(project)
            or closure_facts.get("execution_mode") != execution_mode
            or closure_facts.get("merge_commit") != merge_commit
            or closure_facts.get("implementation_commit") != implementation_commit
            or closure_facts.get("implementation_branch") != scope.get("branch")
            or closure_facts.get("source_host_id") != source_host_id
            or closure_facts.get("source_task_id") != source_task_id
            or closure_facts.get("discussion_binding") != discussion_binding
        ):
            raise ProtocolError("archive_checkpoint_invalid", "retained checkpoint facts do not match the implementation")
        documents_receipts = [
            receipt.get("result")
            for receipt in closure.get("receipts", [])
            if receipt.get("phase") == "documents-committed"
        ]
        if len(documents_receipts) != 1:
            raise ProtocolError("archive_checkpoint_invalid", "closure lacks one document receipt")
        if execution_mode == "isolated-worktree-v1":
            if (
                closure_facts.get("worktree_path") != scope.get("worktree_path")
                or closure_facts.get("documentation_proposals")
                != [item["path"] for item in normalized_proposals]
                or documents_receipts[0].get("proposal_outcomes") != normalized_proposals
            ):
                raise ProtocolError(
                    "archive_checkpoint_invalid",
                    "proposal outcomes are not bound to the retained checkpoint",
                )
        elif normalized_proposals:
            raise ProtocolError(
                "archive_authority_invalid",
                "exclusive checkout archives do not accept isolated document proposals",
            )
        implementation["state"] = "archived"
        implementation["record_revision"] += 1
        _store_implementation(implementation_record, implementation)
        archive_result_id = "AR-" + uuid.UUID(request["idempotency_key"]).hex
        archive_data = {
            "archive_result_id": archive_result_id,
            "closure_checkpoint": dict(request["closure_checkpoint"]),
            "documentation_proposals": normalized_proposals,
            "effective_phase_result_id": phase_result["result_id"],
            "execution_mode": execution_mode,
            "implementation_id": implementation["implementation_id"],
            "implementation_commit": implementation_commit,
            "merge_commit": merge_commit,
            "source_checkpoint_id": checkpoint["checkpoint_id"],
            "source_identity": checkpoint["published_identity"],
            "topic_id": request["actor_topic_id"],
        }
        records["Phase Results"].append(
            {
                "result_id": archive_result_id,
                "result_kind": "archive-result",
                "state": "completed",
                "record_revision": 1,
                "data_json": _canonical_json(archive_data),
            }
        )
        topic["current_phase"] = 4
        topic["phase_state"] = "completed"
        topic["record_revision"] = int(topic["record_revision"]) + 1
        next_revision = ledger_revision + 1
        result = {
            "ok": True, "state": "archive-complete", "topic_state": "open",
            "idempotent_replay": False, "project_id": request["project_id"],
            "tree_id": request["tree_id"], "topic_id": request["actor_topic_id"],
            "ledger_revision": next_revision, "record_revision": topic["record_revision"],
            "archive_result_id": archive_result_id, "effective_sources": {
                "phase_result_id": phase_result["result_id"],
                "source_checkpoint_id": checkpoint["checkpoint_id"],
                "source_identity": checkpoint["published_identity"],
            },
            "phase_results": {"implementation": "completed", "archive": "completed"},
            "cleanup_responsibility": execution_mode,
            "conditional_close": "pending-topic-coordination-check",
        }
        _write_ledger_transaction(
            ledger_path, frontmatter, records, request,
            ledger_revision=next_revision, event_type="archive-completed", result=result,
        )
        return result


def _close_archived_topic(request: dict[str, Any]) -> dict[str, Any]:
    _, ledger_path, _, lock_path, owner_ref = _evolution_paths(request, allow_tree_topic=True)
    _expect_keys(
        request,
        {"protocol_version", "operation", "project_path", "project_id", "tree_id", "actor_topic_id", "actor_conversation_ref", "expected_ledger_revision", "expected_topic_revision", "idempotency_key"},
        "close-archived-topic request",
    )
    _validate_uuid4(request["idempotency_key"], "idempotency_key")
    with lock_path.open("a+b") as lock_stream:
        _flock_with_timeout(lock_stream)
        frontmatter, records = _load_records(ledger_path)
        replay = _idempotent_result(records, request)
        if replay is not None:
            return replay
        topic = _record_by_id(records["Current Topics"], "topic_id", request["actor_topic_id"], "topic_id")
        ledger_revision, _ = _validate_revisions(request, frontmatter, topic)
        _verify_topic_owner(records, request["actor_topic_id"], owner_ref)
        archive_results = [
            item for item in records["Phase Results"]
            if item.get("result_kind") == "archive-result"
            and item.get("state") == "completed"
            and _json_field(item, "data_json", "archive result").get("topic_id") == request["actor_topic_id"]
        ]
        if len(archive_results) != 1 or topic.get("phase_state") != "completed":
            raise ProtocolError("archive_authority_invalid", "topic has no unique completed archive result")
        blockers: list[str] = []
        if any(item["state"] == "pending" for item in _topic_snapshot(records, request["actor_topic_id"])["impacts"]):
            blockers.append("pending-impacts")
        active_run_states = {
            "prepared",
            "setup-pending",
            "ready",
            "active",
            "completion-claimed",
            "completion-pending",
            "blocked",
            "failed",
            "outcome-unknown",
        }
        if any(
            item.get("state") in active_run_states
            and _json_field(item, "data_json", "phase run").get("source_topic_id")
            == request["actor_topic_id"]
            for item in records["Phase Runs"]
        ):
            blockers.append("active-or-queued-runs")
        implementations = [
            _json_field(item, "data_json", "implementation")
            for item in records["Dependencies and Active Implementations"]
            if item.get("topic_id") == request["actor_topic_id"]
        ]
        if any(item.get("state") != "archived" for item in implementations):
            blockers.append("active-implementations")
        if any(
            item.get("topic_id") == request["actor_topic_id"]
            and item.get("state") not in {"completed", "cancelled", "superseded"}
            for item in records["Pending Document Writes"]
        ):
            blockers.append("unverified-coordination")
        for relation in records["Relations and Coverage"]:
            if request["actor_topic_id"] not in {
                relation.get("source_topic_id"),
                relation.get("target_topic_id"),
            }:
                continue
            relation_type = relation.get("relation_type")
            relation_state = relation.get("state")
            pending_absorption = relation_type == "absorbs" and relation_state in {
                "pending", "outcome-unknown"
            }
            active_blocker = relation_type in {"blocks", "affected"} and relation_state != "resolved"
            if pending_absorption or active_blocker:
                blockers.append("pending-absorption-or-blockers")
                break
        next_revision = ledger_revision + 1
        if blockers:
            result = {
                "ok": True, "state": "archive-complete", "topic_state": "open",
                "idempotent_replay": False, "project_id": request["project_id"],
                "tree_id": request["tree_id"], "topic_id": request["actor_topic_id"],
                "ledger_revision": next_revision, "record_revision": topic["record_revision"],
                "blockers": sorted(set(blockers)), "conditional_close": "topic-open",
            }
            _write_ledger_transaction(ledger_path, frontmatter, records, request, ledger_revision=next_revision, event_type="topic-close-deferred", result=result)
            return result
        topic["topic_state"] = "closed"
        topic["record_revision"] = int(topic["record_revision"]) + 1
        result = {
            "ok": True, "state": "closed", "topic_state": "closed",
            "idempotent_replay": False, "project_id": request["project_id"],
            "tree_id": request["tree_id"], "topic_id": request["actor_topic_id"],
            "ledger_revision": next_revision, "record_revision": topic["record_revision"],
            "blockers": [], "conditional_close": "topic-closed",
        }
        _write_ledger_transaction(ledger_path, frontmatter, records, request, ledger_revision=next_revision, event_type="topic-closed", result=result)
        return result


def _read_topic(request: dict[str, Any], *, validate_only: bool = False) -> dict[str, Any]:
    project, ledger_path, topic_path, lock_path, owner_ref = _evolution_paths(request, query=True)
    allowed = {"protocol_version", "operation", "project_path", "project_id", "tree_id", "actor_topic_id", "actor_conversation_ref"}
    _expect_keys(request, allowed, f"{request['operation']} request")
    with lock_path.open("a+b") as lock_stream:
        _flock_with_timeout(lock_stream)
        frontmatter, records = _load_records(ledger_path)
        _verify_topic_owner(records, request["actor_topic_id"], owner_ref)
        _validate_pending_writes(ledger_path, records)
        checkpoints = _validate_checkpoints(project, records)
        handoff_count = _validate_handoffs(records)
        topic_record = _record_by_id(records["Current Topics"], "topic_id", request["actor_topic_id"], "topic_id")
        snapshot = _topic_snapshot(records, request["actor_topic_id"])
        active_questions = [question for question in snapshot["questions"] if question["state"] == "active"]
        if len(active_questions) > 1:
            raise ProtocolError("state_corrupt", "topic has more than one active question")
        current_digest = _sha256(_require_regular_nosymlink(topic_path, "topic document"))
        pending = [dict(record) for record in records["Pending Document Writes"]]
        if validate_only:
            return {
                "ok": True, "state": "valid", "ledger_revision": int(frontmatter["ledger_revision"]),
                "record_revision": topic_record["record_revision"], "topic_document_sha256": current_digest,
                "active_question_count": len(active_questions), "pending_document_write_count": len([item for item in pending if item["state"] != "completed"]),
                "checkpoint_count": len(checkpoints),
                "handoff_count": handoff_count,
            }
        return {
            "ok": True, "state": "read", "ledger_revision": int(frontmatter["ledger_revision"]),
            "record_revision": topic_record["record_revision"], "topic_document_sha256": current_digest,
            "active_question": active_questions[0] if active_questions else None,
            "pending_document_writes": pending, "checkpoints": checkpoints, **snapshot,
        }


def _build_operation_registry() -> OperationRegistry:
    """Return the sole authority for all 66 stable operation names."""

    return OperationRegistry(
        [
            ("bootstrap", _bootstrap),
            ("discover-context", _discover_context),
            ("locate-context", _discover_context),
            ("initialize-document-context", _initialize_document_context),
            ("prepare-phase-run", _prepare_phase_run),
            ("route-phase", _prepare_phase_run),
            ("prepare-wrapper-phase-run", _prepare_wrapper_phase_run),
            ("prepare-no-code-integration-run", _prepare_no_code_integration_run),
            ("authorize-continuous-flow", _authorize_continuous_flow),
            ("phase-ready", lambda request: _transition_phase_attempt(request, "ready", "phase-attempt-ready")),
            ("authorize-phase-carrier", _authorize_phase_carrier),
            ("claim-phase-carrier", _claim_phase_carrier),
            ("phase-activate", lambda request: _transition_phase_attempt(request, "active", "phase-attempt-activated")),
            ("revoke-phase-authorization", _revoke_phase_authorization),
            ("supersede-phase-run", _supersede_phase_run),
            ("claim-phase-completion", _claim_phase_completion),
            ("complete-phase-run", _complete_phase_run),
            ("finalize-phase-run", _finalize_phase_run),
            ("cancel-phase-run", lambda request: _transition_phase_attempt(request, "cancelled", "phase-run-cancelled")),
            ("fail-phase-run", lambda request: _transition_phase_attempt(request, "failed", "phase-run-failed")),
            ("block-phase-run", lambda request: _transition_phase_attempt(request, "blocked", "phase-run-blocked")),
            ("phase-outcome-unknown", lambda request: _transition_phase_attempt(request, "outcome-unknown", "phase-run-outcome-unknown")),
            ("retry-phase-run", _retry_phase_run),
            ("reconcile-phase-run", _reconcile_phase_run),
            ("read-phase-run", _read_phase_run),
            ("reopen-phase", _reopen_phase),
            ("prepare-topic-update", _prepare_topic_update),
            ("apply-document-write", _apply_document_write),
            ("complete-document-write", _complete_document_write),
            ("reconcile-document-write", _reconcile_document_write),
            ("prepare-checkpoint", _prepare_checkpoint),
            ("cancel-checkpoint", _cancel_checkpoint),
            ("publish-git-checkpoint", _publish_git_checkpoint),
            ("publish-non-git-checkpoint", _publish_non_git_checkpoint),
            ("record-checkpoint-outcome-unknown", _record_checkpoint_outcome_unknown),
            ("reconcile-git-checkpoint", _reconcile_git_checkpoint),
            ("reconcile-non-git-checkpoint", _reconcile_non_git_checkpoint),
            ("mark-checkpoint-broken", _mark_checkpoint_broken),
            ("register-active-checkpoint-source", _register_active_checkpoint_source),
            ("prepare-implementation-run", _prepare_implementation_run),
            ("check-implementation-parallelism", _check_implementation_parallelism),
            ("validate-implementation-parallelism", _validate_implementation_parallelism),
            ("activate-implementation-run", _activate_implementation_run),
            ("prepare-source-refresh", _prepare_source_refresh),
            ("ack-source-refresh", _ack_source_refresh),
            ("commit-source-refresh", _commit_source_refresh),
            ("issue-integration-authority-receipt", _issue_integration_authority_receipt),
            ("validate-integration-authority-receipt", _validate_integration_authority_receipt),
            ("record-archive-complete", _record_archive_complete),
            ("close-archived-topic", _close_archived_topic),
            ("repair-checkpoint", _repair_checkpoint),
            ("checkpoint-gc-dry-run", _checkpoint_gc_dry_run),
            ("checkpoint-gc-confirm", _checkpoint_gc_confirm),
            ("reconcile-checkpoint-gc", _reconcile_checkpoint_gc),
            ("prepare-handoff", _prepare_handoff),
            ("bind-handoff", _bind_handoff),
            ("accept-handoff", _accept_handoff),
            ("authorize-handoff-discussion", _authorize_handoff_discussion),
            ("record-handoff-outcome-unknown", lambda request: _transition_handoff_attempt(request, target_state="outcome-unknown", event_type="handoff-outcome-unknown")),
            ("record-handoff-failure", lambda request: _transition_handoff_attempt(request, target_state="failed", event_type="handoff-failed")),
            ("cancel-handoff-attempt", lambda request: _transition_handoff_attempt(request, target_state="cancelled", event_type="handoff-cancelled")),
            ("retry-handoff", _retry_handoff),
            ("reconcile-handoff-attempt", _reconcile_handoff_attempt),
            ("submit-child-result", _submit_child_result),
            ("record-child-result", _record_child_result),
            ("read-handoff", _read_handoff),
            ("read-topic", _read_topic),
            ("validate", lambda request: _read_topic(request, validate_only=True)),
        ]
    )


OPERATION_REGISTRY = _build_operation_registry()
OPERATION_ALIASES = {
    "locate-context": "discover-context",
    "route-phase": "prepare-phase-run",
}


def handle(request: Any) -> dict[str, Any]:
    context = RequestContext.parse(
        request,
        protocol_version=PROTOCOL_VERSION,
        error_type=ProtocolError,
        parse_project_path=_validate_project_path,
        parse_string=_expect_string,
        known_operations=OPERATION_REGISTRY.names,
    )
    return OPERATION_REGISTRY.dispatch(
        context,
        unsupported=lambda: ProtocolError(
            "unsupported_operation", "discussion protocol operation is unsupported"
        ),
    )


def main() -> int:
    request: Any = None
    try:
        raw = sys.stdin.buffer.read(MAX_REQUEST_BYTES + 1)
        if len(raw) > MAX_REQUEST_BYTES:
            raise ProtocolError(
                "invalid_request", f"request exceeds {MAX_REQUEST_BYTES} bytes"
            )
        request = json.loads(raw)
        response = handle(request)
        returncode = 0
    except (json.JSONDecodeError, UnicodeDecodeError) as error:
        protocol_error = ProtocolError("invalid_json", "stdin must contain one JSON value", cause=str(error))
        response = _response_error(protocol_error)
        print(protocol_error.message, file=sys.stderr)
        returncode = 1
    except ProtocolError as error:
        response = _response_error(error)
        print(error.message, file=sys.stderr)
        returncode = 1
    except Exception as error:
        protocol_error = ProtocolError(
            "internal_error",
            "discussion protocol encountered an internal error",
            cause=type(error).__name__,
            context=_unexpected_error_context(request),
        )
        response = _response_error(protocol_error)
        print(protocol_error.message, file=sys.stderr)
        returncode = 1
    sys.stdout.write(_canonical_json(response) + "\n")
    return returncode


if __name__ == "__main__":
    raise SystemExit(main())
