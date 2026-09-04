#!/usr/bin/env python3
"""JSON CLI and central dispatch for persistent design discussions."""

from __future__ import annotations

import fcntl
import hashlib
import json
import os
from pathlib import Path
import stat
import subprocess
import sys
import uuid
from typing import Any

sys.path.insert(0, str(Path(__file__).parent))
from discussion_core import OperationRegistry, RequestContext

from discussion_core.state import (
    IDENTITY_RE,
    LEDGER_SECTION_NAMES,
    ROOT_SLUG_RE,
    SHA256_RE,
    ProtocolError,
    _active_pending_write,
    _append_event,
    _atomic_replace,
    _canonical_json,
    _coordination_root,
    _evolution_paths,
    _expect_keys,
    _expect_string,
    _flock_with_timeout,
    _git_common_dir,
    _idempotent_result,
    _inject_failure,
    _is_exact_creation_replay,
    _json_field,
    _ledger_sections,
    _load_records,
    _new_identity,
    _parse_frontmatter,
    _parse_record_section,
    _persist_ledger,
    _project_lock_name,
    _record_by_id,
    _render_records_ledger,
    _require_regular_nosymlink,
    _sha256,
    _topic_snapshot,
    _validate_project_path,
    _validate_revisions,
    _validate_uuid4,
    _verify_ledger_digest,
    _verify_topic_path_authority,
    _verify_topic_owner,
)
from discussion_core.checkpoints import (
    _cancel_checkpoint,
    _checkpoint_gc_confirm,
    _checkpoint_gc_dry_run,
    _mark_checkpoint_broken,
    _prepare_checkpoint,
    _publish_git_checkpoint,
    _publish_non_git_checkpoint,
    _reconcile_checkpoint_gc,
    _reconcile_git_checkpoint,
    _reconcile_non_git_checkpoint,
    _record_checkpoint_outcome_unknown,
    _repair_checkpoint,
    _validate_checkpoints,
)
from discussion_core.handoffs import (
    _accept_handoff,
    _authorize_handoff_discussion,
    _bind_handoff,
    _prepare_handoff,
    _read_handoff,
    _reconcile_handoff_attempt,
    _record_child_result,
    _retry_handoff,
    _submit_child_result,
    _transition_handoff_attempt,
    _validate_handoffs,
)
from discussion_core.phase_runs import (
    _authorize_continuous_flow,
    _authorize_phase_carrier,
    _claim_phase_carrier,
    _claim_phase_completion,
    _complete_phase_run,
    _finalize_phase_run,
    _prepare_no_code_integration_run,
    _prepare_phase_run,
    _prepare_wrapper_phase_run,
    _read_phase_run,
    _reconcile_phase_run,
    _reopen_phase,
    _retry_phase_run,
    _revoke_phase_authorization,
    _supersede_phase_run,
    _transition_phase_attempt,
)
from discussion_core.topic_dependencies import (
    derived_gate,
    evaluate_topic_gate,
    release_topic_gate,
    reclose_directly_affected,
    require_open_gate,
    update_topic_dependency,
)


PROTOCOL_VERSION = 1
EXPLICIT_ENTRY_MODES = {
    "explicit-skill",
    "explicit-interface-name",
    "explicit-sustained-design",
}
STATELESS_ENTRY_MODE = "ordinary-consultation"
MAX_REQUEST_BYTES = 64 * 1024


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
        "document_ownership_conflict": "调用者不是当前话题文档写入所有者。",
        "document_write_before_conflict": "话题文档已偏离待写入载荷的准备基线。",
        "document_write_payload_damaged": "待写入载荷缺失或摘要损坏。",
        "document_write_orphan_conflict": "孤立载荷与精确重放请求的类型或摘要不匹配。",
        "document_write_reconciliation_required": "存在已确认但未完成的文档写入，必须先恢复协调。",
        "document_write_state_conflict": "待写入记录当前状态不允许此操作。",
        "document_write_verification_failed": "文档写入后的字节校验失败。",
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
        "topic_gate_closed": "当前话题存在未放行的需求依赖门禁。",
        "topic_dependency_cycle": "话题依赖会形成循环，未写入任何状态。",
        "topic_dependency_duplicate": "活动话题依赖边重复。",
        "topic_dependency_state_conflict": "话题依赖状态冲突。",
        "topic_dependency_phase_conflict": "话题依赖只能在讨论 Phase 0 或 1 中修改。",
        "topic_dependency_ownership_conflict": "只有依赖方话题可变更或放行依赖。",
        "topic_dependency_evidence_unavailable": "所需的当前权威证据不可用。",
        "topic_gate_evaluation_stale": "门禁评估已过期，请重新评估并确认。",
    }
    detail: dict[str, Any] = {
        "code": error.code,
        "message": error.message,
        "message_zh": chinese_messages.get(error.code, "讨论协议操作失败。"),
        "retryable": error.retryable,
        "cause": error.cause,
    }
    if error.context:
        detail["context"] = error.context
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


def _expect_integer(value: Any, label: str, minimum: int, maximum: int) -> int:
    if (
        not isinstance(value, int)
        or isinstance(value, bool)
        or value < minimum
        or value > maximum
    ):
        raise ProtocolError(
            "invalid_request",
            f"{label} must be an integer from {minimum} through {maximum}",
        )
    return value


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
        "## Goal\n\n- None.\n\n"
        "## Background and Current State\n\n- None.\n\n"
        "## Scope\n\n- None.\n\n"
        "## Non-goals\n\n- None.\n\n"
        "## Users and Key Scenarios\n\n- None.\n\n"
        "## Confirmed Decisions\n\n- None.\n\n"
        "## Candidate Solution\n\n- None.\n\n"
        "## Tentative Assumptions\n\n- None.\n\n"
        "## Facts\n\n- Persistent discussion workspace initialized.\n\n"
        "## Constraints\n\n- None.\n\n"
        "## Acceptance Conditions\n\n- None.\n\n"
        "## Pending Questions\n\n- The first substantive question is asked after bootstrap verification.\n\n"
        "## Decision Evolution\n\n- None.\n"
    ).encode("utf-8")


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
    return _render_records_ledger(
        {
            "schema_version": "3",
            "project_id": project_id,
            "tree_id": tree_id,
            "creation_idempotency_key": idempotency_key,
            "creation_fingerprint": request_fingerprint,
            "ledger_revision": "1",
            "event_count": "1",
            "project_manifest_path": str(project_manifest_path),
        },
        records,
    )


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
        "project_id": manifest["project_id"],
        "tree_id": manifest["tree_id"],
        "topic_id": manifest["topic_id"],
    }
    try:
        ledger_data = _require_regular_nosymlink(ledger_path, "ledger")
        observed_ledger_frontmatter = _parse_frontmatter(ledger_data, "ledger")
        try:
            observed_ledger_revision = int(
                observed_ledger_frontmatter["ledger_revision"]
            )
        except (KeyError, TypeError, ValueError):
            observed_ledger_revision = None
        if observed_ledger_revision is not None and observed_ledger_revision > 0:
            context["ledger_revision"] = observed_ledger_revision
        topic_data = _require_regular_nosymlink(topic_path, "topic document")
        ledger_frontmatter, records = _load_records(ledger_path)
        topic_frontmatter = _parse_frontmatter(topic_data, "topic document")
        expected_ledger_identity = {
            "project_id": manifest["project_id"],
            "tree_id": manifest["tree_id"],
            "project_manifest_path": str(manifest_path),
        }
        for field, expected_value in expected_ledger_identity.items():
            if ledger_frontmatter.get(field) != expected_value:
                raise ProtocolError("state_corrupt", f"ledger has invalid {field}")
        try:
            ledger_revision = int(ledger_frontmatter["ledger_revision"])
            event_count = int(ledger_frontmatter["event_count"])
        except (KeyError, TypeError, ValueError) as error:
            raise ProtocolError(
                "state_corrupt", "ledger revisions are invalid"
            ) from error
        if ledger_revision < 1 or event_count < 1:
            raise ProtocolError("state_corrupt", "ledger revisions are invalid")
        context.update({"ledger_revision": ledger_revision})
        if not _is_exact_creation_replay(
            ledger_frontmatter,
            idempotency_key=idempotency_key,
            request_fingerprint=request_fingerprint,
        ):
            raise ProtocolError(
                "discussion_already_initialized",
                "the project already has a different persistent discussion root",
                context=context,
            )
        topic_records = [
            record
            for record in records["Current Topics"]
            if record.get("topic_id") == manifest["topic_id"]
        ]
        if len(topic_records) != 1:
            raise ProtocolError(
                "state_corrupt", "project root does not identify one ledger topic"
            )
        topic_record = topic_records[0]
        expected_topic_record = {
            "topic_id": manifest["topic_id"],
            "root_slug": root_slug,
            "topic_document_path": str(topic_path),
        }
        if any(
            topic_record.get(field) != expected
            for field, expected in expected_topic_record.items()
        ):
            raise ProtocolError("state_corrupt", "project root topic identity is invalid")
        record_revision = topic_record.get("record_revision")
        if (
            not isinstance(record_revision, int)
            or isinstance(record_revision, bool)
            or record_revision < 1
            or record_revision > ledger_revision
        ):
            raise ProtocolError("state_corrupt", "project root topic revision is invalid")
        context["record_revision"] = record_revision
        expected_topic_frontmatter = {
            "schema_version": "1",
            "project_id": manifest["project_id"],
            "tree_id": manifest["tree_id"],
            "topic_id": manifest["topic_id"],
            "parent_topic_id": "null",
        }
        for field, expected_value in expected_topic_frontmatter.items():
            if topic_frontmatter.get(field) != expected_value:
                raise ProtocolError("state_corrupt", f"topic document has invalid {field}")
        try:
            document_revision = int(topic_frontmatter["topic_revision"])
        except (KeyError, TypeError, ValueError) as error:
            raise ProtocolError(
                "state_corrupt", "topic document revision is invalid"
            ) from error
        if document_revision < 1 or document_revision > record_revision:
            raise ProtocolError("state_corrupt", "topic document revision is invalid")
        _validate_pending_writes(ledger_path, records)
    except ProtocolError as error:
        error.context = {**context, **error.context}
        raise
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
        "ledger_revision": ledger_revision,
        "topic_revision": record_revision,
        "event_count": event_count,
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
    "refresh-requirement-narrative",
}
IMPACT_ACTIONS = {"keep", "adjust", "replace", "discard"}
QUESTION_ACTIONS = {"resume", "adjust", "invalidate"}


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
        if item["state"] in {"active", "suspended"}
    ] or ["- None."]
    narrative = snapshot.get("requirement_narrative") or {}

    def narrative_lines(field: str) -> list[str]:
        return [f"- {item}" for item in narrative.get(field, [])] or ["- None."]

    goal = narrative.get("goal", "None.")
    evolution_lines = narrative_lines("direction_change_summary")
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
        "## Goal\n\n"
        + goal
        + "\n\n## Background and Current State\n\n"
        + "\n".join(narrative_lines("background"))
        + "\n\n## Scope\n\n"
        + "\n".join(narrative_lines("scope"))
        + "\n\n## Non-goals\n\n"
        + "\n".join(narrative_lines("non_goals"))
        + "\n\n## Users and Key Scenarios\n\n"
        + "\n".join(narrative_lines("scenarios"))
        + "\n\n"
        "## Confirmed Decisions\n\n"
        + "\n".join(decision_lines)
        + "\n\n## Candidate Solution\n\n"
        + "\n".join(candidate_lines)
        + "\n\n## Tentative Assumptions\n\n"
        + "\n".join(narrative_lines("tentative_assumptions"))
        + "\n\n## Facts\n\n"
        + "\n".join(narrative_lines("facts"))
        + "\n\n## Constraints\n\n"
        + "\n".join(narrative_lines("constraints"))
        + "\n\n## Acceptance Conditions\n\n"
        + "\n".join(narrative_lines("acceptance_conditions"))
        + "\n\n"
        "## Pending Questions\n\n"
        + "\n".join(question_lines)
        + "\n\n## Decision Evolution\n\n"
        + "\n".join(evolution_lines)
        + "\n"
    ).encode("utf-8")


def _validate_mutation(value: Any, key: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ProtocolError("invalid_request", "mutation must be an object")
    mutation_type = value.get("type")
    if mutation_type not in SUBSTANTIVE_MUTATIONS:
        raise ProtocolError("invalid_request", "mutation type is unsupported")
    return value


def _single_active_question_record(
    records: dict[str, list[dict[str, Any]]],
    *,
    topic_id: str,
    multiple_active_message: str,
) -> tuple[dict[str, Any], dict[str, Any]] | None:
    active_question_record = None
    for record in records["Pending Items"]:
        if record.get("topic_id") == topic_id and record.get("item_kind") == "question":
            question = _json_field(record, "data_json", "question")
            if question["state"] == "active":
                if active_question_record is not None:
                    raise ProtocolError("state_corrupt", multiple_active_message)
                active_question_record = (record, question)
    return active_question_record


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
        active_question_record = _single_active_question_record(
            records,
            topic_id=topic_id,
            multiple_active_message="topic has more than one active question",
        )
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
        if active_question_record is not None:
            question_record, question = active_question_record
            question["state"] = "answered"
            question["answered_by_decision_id"] = decision_id
            question_record["data_json"] = _canonical_json(question)
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
        active_question_record = _single_active_question_record(
            records,
            topic_id=topic_id,
            multiple_active_message="topic has multiple active questions",
        )
        suspended_question_id = None
        if active_question_record is not None:
            question_record, question = active_question_record
            question["state"] = "suspended"
            question_record["data_json"] = _canonical_json(question)
            suspended_question_id = question["question_id"]
        idea_id = f"I-{seed}"
        idea = {
            "idea_id": idea_id,
            "summary": _expect_string(mutation["summary"], "mutation.summary", max_bytes=4096),
            "suspended_question_id": suspended_question_id,
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
    elif mutation_type == "refresh-requirement-narrative":
        fields = {
            "type", "goal", "background", "scope", "non_goals", "scenarios",
            "tentative_assumptions", "facts", "constraints", "acceptance_conditions",
            "direction_change_summary",
        }
        _expect_keys(mutation, fields, "refresh-requirement-narrative mutation")

        def bounded_lines(field: str) -> list[str]:
            value = mutation[field]
            if not isinstance(value, list) or len(value) > 64:
                raise ProtocolError("invalid_request", f"mutation.{field} must be a bounded array")
            if field == "direction_change_summary" and len(value) > 1:
                raise ProtocolError(
                    "invalid_request",
                    "mutation.direction_change_summary must contain at most one current note",
                )
            lines = [
                _expect_string(item, f"mutation.{field} item", max_bytes=2048)
                for item in value
            ]
            if len(set(lines)) != len(lines):
                raise ProtocolError("invalid_request", f"mutation.{field} values must be unique")
            return lines

        data = {
            "narrative_id": f"RN-{topic_id.removeprefix('topic-')}",
            "goal": _expect_string(mutation["goal"], "mutation.goal", max_bytes=2048),
            **{field: bounded_lines(field) for field in fields - {"type", "goal"}},
        }
        existing = [
            record for record in records["Pending Items"]
            if record.get("topic_id") == topic_id
            and record.get("item_kind") == "requirement-narrative"
        ]
        if len(existing) > 1:
            raise ProtocolError("state_corrupt", "topic has multiple requirement narratives")
        record = {
            "item_id": data["narrative_id"],
            "item_kind": "requirement-narrative",
            "topic_id": topic_id,
            "data_json": _canonical_json(data),
        }
        if existing:
            existing[0].update(record)
        else:
            records["Pending Items"].append(record)
        result["requirement_narrative_id"] = data["narrative_id"]
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
    project, ledger_path, topic_path, lock_path, owner_ref = _evolution_paths(request, allow_tree_topic=True)
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
        topic_path = _verify_topic_path_authority(topic_record, topic_path, records=records)
        ledger_revision, topic_revision = _validate_revisions(request, frontmatter, topic_record)
        _verify_topic_owner(
            records,
            request["actor_topic_id"],
            owner_ref,
            allow_active_grilling=True,
        )
        require_open_gate(records, request["actor_topic_id"])
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
        if suspended_questions and mutation["type"] not in {"confirm-decision", "resolve-inserted-idea"}:
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
        invalidated_dependencies: list[str] = []
        if mutation.get("type") == "resolve-impact" and mutation.get("action") in {"adjust", "replace", "discard"}:
            invalidated_dependencies = reclose_directly_affected(
                next_records, prerequisite_topic_id=request["actor_topic_id"],
                changed_decision_ids={mutation["decision_id"]},
                cause={"decision_id": mutation["decision_id"], "action": mutation["action"], "topic_update_id": f"DW-{uuid.UUID(request['idempotency_key']).hex}"},
                ledger_revision=next_revision,
            )
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
            "invalidated_dependency_ids": invalidated_dependencies,
        }
        _append_event(next_records, request, revision=next_revision, event_type="topic-update-prepared", result=result)
        frontmatter["ledger_revision"] = str(next_revision)
        frontmatter["event_count"] = str(int(frontmatter["event_count"]) + 1)
        if os.environ.get("CODEX_DISCUSSION_TEST_FAILPOINT") == "topic-update-ledger-replace":
            raise OSError("injected topic update ledger replace failure")
        _persist_ledger(ledger_path, frontmatter, next_records)
        return result


def _apply_document_write(request: dict[str, Any]) -> dict[str, Any]:
    project, ledger_path, topic_path, lock_path, owner_ref = _evolution_paths(request, allow_tree_topic=True)
    _expect_keys(
        request,
        {"protocol_version", "operation", "project_path", "project_id", "tree_id", "actor_topic_id", "actor_conversation_ref", "expected_ledger_revision", "expected_topic_revision", "idempotency_key", "document_write_id"},
        "apply-document-write request",
    )
    _validate_uuid4(request["idempotency_key"], "idempotency_key")
    with lock_path.open("a+b") as lock_stream:
        _flock_with_timeout(lock_stream)
        frontmatter, records = _load_records(ledger_path)
        replay = _idempotent_result(records, request)
        if replay is not None:
            return replay
        topic_record = _record_by_id(records["Current Topics"], "topic_id", request["actor_topic_id"], "topic_id")
        topic_path = _verify_topic_path_authority(topic_record, topic_path, records=records)
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
        if write.get("topic_path") != str(topic_path):
            raise ProtocolError(
                "state_corrupt",
                "pending document write path does not match the authoritative topic path",
            )
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
        write["state"] = "completed"
        result = {
            "ok": True, "state": "completed", "idempotent_replay": False,
            "project_id": request["project_id"], "tree_id": request["tree_id"], "topic_id": request["actor_topic_id"],
            "ledger_revision": next_revision, "record_revision": next_topic_revision,
            "document_write_id": write["document_write_id"], "document_verified": True,
            "after_sha256": write["after_sha256"],
        }
        _append_event(records, request, revision=next_revision, event_type="document-write-completed", result=result)
        frontmatter["ledger_revision"] = str(next_revision)
        frontmatter["event_count"] = str(int(frontmatter["event_count"]) + 1)
        _persist_ledger(ledger_path, frontmatter, records)
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


def _document_context_replay_response(
    *,
    ledger_path: Path,
    manifest_path: Path,
    manifest: dict[str, str],
    selected_identity: dict[str, str],
    topic_path: Path,
    storage_mode: str,
    idempotency_key: str,
    request_fingerprint: str,
    imported_result_count: int,
) -> dict[str, Any]:
    frontmatter, records = _load_records(ledger_path)
    expected_identity = {
        "project_id": manifest["project_id"],
        "tree_id": manifest["tree_id"],
        "project_manifest_path": str(manifest_path),
    }
    if any(
        frontmatter.get(field) != expected
        for field, expected in expected_identity.items()
    ):
        raise ProtocolError(
            "state_corrupt", "document-only ledger identity is invalid"
        )
    if not _is_exact_creation_replay(
        frontmatter,
        idempotency_key=idempotency_key,
        request_fingerprint=request_fingerprint,
    ):
        raise ProtocolError(
            "context_not_initialized",
            "document-only context is already initialized with a different authorization",
        )
    topic_records = [
        record
        for record in records["Current Topics"]
        if record.get("topic_id") == selected_identity["topic_id"]
    ]
    if len(topic_records) != 1:
        raise ProtocolError(
            "state_corrupt", "document-only initialization topic is not unique"
        )
    topic_record = topic_records[0]
    if topic_record.get("topic_document_path") != str(topic_path):
        raise ProtocolError(
            "state_corrupt", "document-only initialization topic path is invalid"
        )
    topic_revision = topic_record.get("record_revision")
    try:
        ledger_revision = int(frontmatter["ledger_revision"])
    except (KeyError, TypeError, ValueError) as error:
        raise ProtocolError("state_corrupt", "ledger revision is invalid") from error
    if (
        not isinstance(topic_revision, int)
        or isinstance(topic_revision, bool)
        or topic_revision < 1
        or topic_revision > ledger_revision
    ):
        raise ProtocolError(
            "state_corrupt", "document-only topic revision is invalid"
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
        "ledger_revision": ledger_revision,
        "topic_revision": topic_revision,
        "ledger_path": str(ledger_path),
        "topic_document_path": str(topic_path),
        "imported_result_count": imported_result_count,
        "coordination_state": "unknown",
        "idempotent_replay": True,
    }


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
    for field, kind in (
        ("project_id", "project"),
        ("tree_id", "tree"),
        ("topic_id", "topic"),
    ):
        value = manifest[field]
        if not value.startswith(f"{kind}-") or not IDENTITY_RE.fullmatch(value):
            raise ProtocolError(
                "context_identity_conflict",
                f"document-only manifest has invalid {field}",
            )
    if not ROOT_SLUG_RE.fullmatch(manifest["root_slug"]):
        raise ProtocolError(
            "context_identity_conflict",
            "document-only manifest has invalid root_slug",
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
        return _document_context_replay_response(
            ledger_path=ledger_path,
            manifest_path=manifest_path,
            manifest=manifest,
            selected_identity=selected_identity,
            topic_path=topic_path,
            storage_mode=storage_mode,
            idempotency_key=key,
            request_fingerprint=fingerprint,
            imported_result_count=len(imported_results),
        )
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
                return _document_context_replay_response(
                    ledger_path=ledger_path,
                    manifest_path=manifest_path,
                    manifest=manifest,
                    selected_identity=selected_identity,
                    topic_path=topic_path,
                    storage_mode=storage_mode,
                    idempotency_key=key,
                    request_fingerprint=fingerprint,
                    imported_result_count=len(imported_results),
                )
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


def _read_topic(request: dict[str, Any]) -> dict[str, Any]:
    project, ledger_path, topic_path, lock_path, owner_ref = _evolution_paths(request, query=True, allow_tree_topic=True)
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
        topic_path = _verify_topic_path_authority(topic_record, topic_path, records=records)
        snapshot = _topic_snapshot(records, request["actor_topic_id"])
        gate_state = derived_gate(records, request["actor_topic_id"])
        active_question_record = _single_active_question_record(
            records,
            topic_id=request["actor_topic_id"],
            multiple_active_message="topic has more than one active question",
        )
        current_digest = _sha256(_require_regular_nosymlink(topic_path, "topic document"))
        pending = [dict(record) for record in records["Pending Document Writes"]]
        return {
            "ok": True, "state": "read", "ledger_revision": int(frontmatter["ledger_revision"]),
            "record_revision": topic_record["record_revision"], "topic_document_sha256": current_digest,
            "current_phase": topic_record["current_phase"],
            "phase_state": topic_record["phase_state"],
            "review_state": topic_record["review_state"],
            "topic_state": topic_record["topic_state"],
            "active_question_count": 1 if active_question_record is not None else 0,
            "pending_document_write_count": len(
                [item for item in pending if item["state"] != "completed"]
            ),
            "checkpoint_count": len(checkpoints), "handoff_count": handoff_count,
            "active_question": active_question_record[1] if active_question_record else None,
            "pending_document_writes": pending, "checkpoints": checkpoints,
            "derived_gate_state": gate_state,
            "topic_dependencies": [dict(item) for item in records["Topic Dependencies"] if item["dependent_topic_id"] == request["actor_topic_id"]], **snapshot,
        }


def _build_operation_registry() -> OperationRegistry:
    """Return the sole authority for all stable operation names."""

    return OperationRegistry(
        [
            ("bootstrap", _bootstrap),
            ("discover-context", _discover_context),
            ("initialize-document-context", _initialize_document_context),
            ("prepare-phase-run", _prepare_phase_run),
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
            ("phase-outcome-unknown", lambda request: _transition_phase_attempt(request, "outcome-unknown", "phase-run-outcome-unknown")),
            ("retry-phase-run", _retry_phase_run),
            ("reconcile-phase-run", _reconcile_phase_run),
            ("read-phase-run", _read_phase_run),
            ("reopen-phase", _reopen_phase),
            ("prepare-topic-update", _prepare_topic_update),
            ("apply-document-write", _apply_document_write),
            ("prepare-checkpoint", _prepare_checkpoint),
            ("cancel-checkpoint", _cancel_checkpoint),
            ("publish-git-checkpoint", _publish_git_checkpoint),
            ("publish-non-git-checkpoint", _publish_non_git_checkpoint),
            ("record-checkpoint-outcome-unknown", _record_checkpoint_outcome_unknown),
            ("reconcile-git-checkpoint", _reconcile_git_checkpoint),
            ("reconcile-non-git-checkpoint", _reconcile_non_git_checkpoint),
            ("mark-checkpoint-broken", _mark_checkpoint_broken),
            ("repair-checkpoint", _repair_checkpoint),
            ("checkpoint-gc-dry-run", _checkpoint_gc_dry_run),
            ("checkpoint-gc-confirm", _checkpoint_gc_confirm),
            ("reconcile-checkpoint-gc", _reconcile_checkpoint_gc),
            ("prepare-handoff", _prepare_handoff),
            ("update-topic-dependency", update_topic_dependency),
            ("evaluate-topic-gate", evaluate_topic_gate),
            ("release-topic-gate", release_topic_gate),
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
        ]
    )


OPERATION_REGISTRY = _build_operation_registry()


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


def _reject_duplicate_json_keys(
    pairs: list[tuple[str, Any]],
) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ProtocolError("invalid_json", f"duplicate JSON key: {key}")
        result[key] = value
    return result


def _reject_floating_point_json_number(_: str) -> Any:
    raise ProtocolError(
        "invalid_json", "floating-point JSON values are unsupported"
    )


def _reject_non_finite_json_number(_: str) -> Any:
    raise ProtocolError("invalid_json", "non-finite JSON values are unsupported")


def main() -> int:
    request: Any = None
    try:
        raw = sys.stdin.buffer.read(MAX_REQUEST_BYTES + 1)
        if len(raw) > MAX_REQUEST_BYTES:
            raise ProtocolError(
                "invalid_request", f"request exceeds {MAX_REQUEST_BYTES} bytes"
            )
        request = json.loads(
            raw,
            object_pairs_hook=_reject_duplicate_json_keys,
            parse_float=_reject_floating_point_json_number,
            parse_constant=_reject_non_finite_json_number,
        )
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
