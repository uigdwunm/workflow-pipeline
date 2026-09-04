"""Dependency-free schema checks for Topic Dependency ledger records."""

from __future__ import annotations

import json
import re
from typing import Any, Callable


KINDS = {"phase-0-checkpoint", "phase-1-result", "confirmed-decision"}
RECORD_FIELDS = {
    "dependency_id", "record_revision", "dependent_topic_id", "prerequisite_topic_id",
    "requirement_kind", "requirement_summary", "relation_state", "gate_state",
    "accepted_basis_json", "gate_reason_json",
}
SHA256_RE = re.compile(r"[0-9a-f]{64}")


def _object(value: Any, label: str, error: Callable[[str, str], None]) -> dict[str, Any]:
    if not isinstance(value, str):
        error("state_corrupt", f"{label} must be canonical JSON")
    try:
        decoded = json.loads(value)
    except json.JSONDecodeError:
        error("state_corrupt", f"{label} is invalid JSON")
    if not isinstance(decoded, dict) or json.dumps(
        decoded, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ) != value:
        error("state_corrupt", f"{label} must be a canonical JSON object")
    return decoded


def validate_dependency_records(
    records: dict[str, list[dict[str, Any]]], error: Callable[[str, str], None],
) -> None:
    """Validate ledger-only invariants without importing state or protocol owners."""
    topics = {item.get("topic_id") for item in records["Current Topics"]}
    seen_ids: set[str] = set()
    edges: dict[str, set[str]] = {}
    pairs: set[tuple[str, str]] = set()
    for item in records["Topic Dependencies"]:
        if set(item) != RECORD_FIELDS:
            error("state_corrupt", "topic dependency record fields are invalid")
        dep_id = item["dependency_id"]
        if not isinstance(dep_id, str) or not dep_id.startswith("DEP-") or dep_id in seen_ids:
            error("state_corrupt", "topic dependency identity is invalid or duplicated")
        seen_ids.add(dep_id)
        if not isinstance(item["record_revision"], int) or item["record_revision"] < 1:
            error("state_corrupt", "topic dependency revision is invalid")
        dependent, prerequisite = item["dependent_topic_id"], item["prerequisite_topic_id"]
        if dependent not in topics or prerequisite not in topics or dependent == prerequisite:
            error("state_corrupt", "topic dependency endpoints are invalid")
        if item["requirement_kind"] not in KINDS or not isinstance(item["requirement_summary"], str) or not item["requirement_summary"]:
            error("state_corrupt", "topic dependency requirement is invalid")
        if item["relation_state"] not in {"active", "cancelled"} or item["gate_state"] not in {"closed", "open"}:
            error("state_corrupt", "topic dependency state is invalid")
        _object(item["gate_reason_json"], "topic dependency gate_reason_json", error)
        if item["accepted_basis_json"] is not None:
            basis = _object(item["accepted_basis_json"], "topic dependency accepted_basis_json", error)
            decisions = basis.get("decision_authority")
            if (
                basis.get("basis_version") != 1
                or basis.get("dependency_id") != dep_id
                or basis.get("prerequisite_topic_id") != prerequisite
                or basis.get("requirement_kind") != item["requirement_kind"]
                or not isinstance(decisions, list)
                or decisions != sorted(decisions, key=lambda entry: entry.get("decision_id", ""))
                or len({entry.get("decision_id") for entry in decisions}) != len(decisions)
                or any(not isinstance(entry, dict) or set(entry) != {"decision_id", "sha256"} or not isinstance(entry["decision_id"], str) or not isinstance(entry["sha256"], str) or not SHA256_RE.fullmatch(entry["sha256"]) for entry in decisions)
                or set(basis) - {"basis_version", "dependency_id", "prerequisite_topic_id", "requirement_kind", "decision_authority", "authority", "child_result_id"}
            ):
                error("state_corrupt", "topic dependency accepted basis is incoherent")
            authority = basis.get("authority")
            if item["requirement_kind"] == "confirmed-decision":
                valid = isinstance(authority, dict) and set(authority) == {"decision_set_digest"} and isinstance(authority["decision_set_digest"], str) and SHA256_RE.fullmatch(authority["decision_set_digest"]) and decisions
            elif item["requirement_kind"] == "phase-0-checkpoint":
                valid = isinstance(authority, dict) and set(authority) == {"checkpoint_id", "record_revision", "published_identity", "decision_digest"} and isinstance(authority["checkpoint_id"], str) and isinstance(authority["record_revision"], int) and isinstance(authority["published_identity"], str) and isinstance(authority["decision_digest"], str) and SHA256_RE.fullmatch(authority["decision_digest"])
            else:
                valid = isinstance(authority, dict) and set(authority) == {"result_id", "record_revision", "state", "phase_run_id", "affected_decision_ids"} and isinstance(authority["result_id"], str) and isinstance(authority["record_revision"], int) and authority["state"] == "completed" and isinstance(authority["phase_run_id"], str) and isinstance(authority["affected_decision_ids"], list)
            if not valid:
                error("state_corrupt", "topic dependency authority is incoherent")
        elif item["relation_state"] == "active" and item["gate_state"] == "open":
            error("state_corrupt", "open topic dependency requires an accepted basis")
        if item["relation_state"] == "active":
            pair = (dependent, prerequisite)
            if pair in pairs:
                error("state_corrupt", "active topic dependency endpoint pair is duplicated")
            pairs.add(pair)
            edges.setdefault(dependent, set()).add(prerequisite)

    visiting: set[str] = set()
    visited: set[str] = set()

    def visit(node: str) -> None:
        if node in visiting:
            error("state_corrupt", "active topic dependency graph contains a cycle")
        if node in visited:
            return
        visiting.add(node)
        for target in edges.get(node, set()):
            visit(target)
        visiting.remove(node)
        visited.add(node)

    for node in edges:
        visit(node)
