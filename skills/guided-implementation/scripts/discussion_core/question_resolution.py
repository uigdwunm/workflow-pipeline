"""Shared suspended-question mutation and immutable document-write staging."""

from __future__ import annotations

from pathlib import Path
from typing import Any
import uuid

from .state import (
    ProtocolError,
    _canonical_json,
    _mkdirs,
    _require_regular_nosymlink,
    _sha256,
    _write_new_file,
)


def resolve_suspended_question(
    record: dict[str, Any],
    question: dict[str, Any],
    *,
    action: str,
    adjusted_prompt: str | None,
) -> dict[str, Any]:
    """Apply one already-validated resolution to a suspended question record."""
    question["state"] = "invalidated" if action == "invalidate" else "active"
    if action == "adjust":
        if adjusted_prompt is None:
            raise ProtocolError("state_corrupt", "adjusted suspended question lacks a prompt")
        question["prompt"] = adjusted_prompt
    record["data_json"] = _canonical_json(question)
    return question


def stage_pending_document_write(
    records: dict[str, list[dict[str, Any]]],
    *,
    ledger_path: Path,
    topic_path: Path,
    topic_id: str,
    owner_ref: str,
    idempotency_key: str,
    before: bytes,
    after: bytes,
    recover_exact_orphan: bool,
) -> tuple[dict[str, Any], bool]:
    """Persist immutable payload bytes and their pending ledger record together.

    Ordinary topic updates can claim only their exact deterministic orphan. Child
    handoffs use the same primitive with recovery disabled, so any existing
    payload fails before the ledger transaction can publish partial state.
    """
    write_id = f"DW-{uuid.UUID(idempotency_key).hex}"
    payload_path = ledger_path.parent / "pending-writes" / f"{write_id}.payload"
    _mkdirs(payload_path.parent, [])
    recovered_orphan = False

    if recover_exact_orphan:
        owned_payload_paths = {
            Path(item["payload_path"]) for item in records["Pending Document Writes"]
        }
        observed_payload_paths = {
            item for item in payload_path.parent.iterdir() if item.is_file()
        }
        orphan_payload_paths = observed_payload_paths - owned_payload_paths
        if orphan_payload_paths:
            if orphan_payload_paths != {payload_path}:
                raise ProtocolError(
                    "orphaned_document_write",
                    "an unrelated orphan payload must be recovered by its exact request",
                )
            orphan_bytes = _require_regular_nosymlink(
                payload_path, "orphan pending document payload"
            )
            if orphan_bytes != after or _sha256(orphan_bytes) != _sha256(after):
                raise ProtocolError(
                    "document_write_orphan_conflict",
                    "the deterministic orphan payload does not match this typed request",
                )
            recovered_orphan = True
        else:
            _write_new_file(payload_path, after, [])
    else:
        try:
            _write_new_file(payload_path, after, [])
        except ProtocolError as error:
            if error.code != "initialization_conflict":
                raise
            raise ProtocolError(
                "document_write_orphan_conflict",
                "handoff document payload already exists",
            ) from error

    payload_path.chmod(0o400)
    write_record = {
        "document_write_id": write_id,
        "topic_id": topic_id,
        "owner_ref": owner_ref,
        "topic_path": str(topic_path),
        "payload_path": str(payload_path),
        "before_sha256": _sha256(before),
        "after_sha256": _sha256(after),
        "state": "confirmed-but-pending",
    }
    records["Pending Document Writes"].append(write_record)
    return write_record, recovered_orphan
