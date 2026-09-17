"""Shared record builders and scenario bases for focused Ticket-07 CLI tests."""

from __future__ import annotations

from pathlib import Path
import sys
from typing import Any

sys.path.insert(0, str(Path(__file__).parent))
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src/shared/scripts"))

from test_discussion_protocol import (
    DiscussionProtocolScenarioFixture,
    DiscussionProtocolTestSupport,
)


def add_topic(
    records: dict[str, list[dict[str, Any]]], *, topic_id: str, root_slug: str,
    parent_topic_id: str | None, phase: int = 0,
) -> None:
    """Add the minimal valid topic fixture shared by dependency scenarios."""
    records["Current Topics"].append({
        "topic_id": topic_id, "record_revision": 1, "root_slug": root_slug,
        "parent_topic_id": parent_topic_id, "current_phase": phase,
        "phase_state": "active", "review_state": "unreviewed", "topic_state": "open",
        "topic_document_path": None,
    })


def add_binding(
    records: dict[str, list[dict[str, Any]]], *, topic_id: str, conversation_ref: str,
) -> None:
    """Bind a fixture topic to its active conversation."""
    records["Conversation Bindings"].append({
        "topic_id": topic_id, "conversation_ref": conversation_ref,
        "binding_state": "active", "record_revision": 1, "handoff_id": None,
        "attempt_id": None,
    })


def add_closed_dependency(
    records: dict[str, list[dict[str, Any]]], *, dependency_id: str,
    dependent_topic_id: str, prerequisite_topic_id: str, requirement_kind: str,
    requirement_summary: str, gate_reason_json: str,
) -> None:
    """Add one standard closed dependency fixture without duplicating envelopes."""
    records["Topic Dependencies"].append({
        "dependency_id": dependency_id, "record_revision": 1,
        "dependent_topic_id": dependent_topic_id,
        "prerequisite_topic_id": prerequisite_topic_id,
        "requirement_kind": requirement_kind, "requirement_summary": requirement_summary,
        "relation_state": "active", "gate_state": "closed", "accepted_basis_json": None,
        "gate_reason_json": gate_reason_json,
    })
