"""Dependency-record lifecycle and direct invalidation operations.

This module owns mutable dependency records.  It deliberately does not decide
which authority can satisfy a record; that is the authority module's concern.
"""

from __future__ import annotations

import uuid
from typing import Any

from .state import (
    ProtocolError, _canonical_json, _evolution_paths, _expect_keys,
    _flock_with_timeout, _idempotent_result, _inject_failure, _load_records,
    _record_by_id, _sha256, _validate_revisions, _validate_uuid4,
    _verify_topic_owner, _write_ledger_transaction,
)
from .topic_dependency_schema import (
    DEPENDENCY_AUTHORITY_KINDS, authority_descriptor, canonical_object,
    validate_dependency_records,
)
from .topic_dependency_authority import (
    authority_candidates, normalize_authority_selection,
    published_stage_entry_authority,
)


def _state_corrupt(code: str, message: str) -> None:
    raise ProtocolError(code, message)


def dependency_context(
    *, dependent_topic_id: str | None = None,
    prerequisite_topic_id: str | None = None,
    dependency_id: str | None = None,
    expected_dependency_revision: int | None = None,
    actual_dependency_revision: int | None = None,
    phase: int | None = None,
) -> dict[str, Any]:
    return {
        key: value for key, value in {
            "dependent_topic_id": dependent_topic_id,
            "prerequisite_topic_id": prerequisite_topic_id,
            "dependency_id": dependency_id,
            "expected_dependency_revision": expected_dependency_revision,
            "actual_dependency_revision": actual_dependency_revision,
            "phase": phase,
        }.items() if value is not None
    }


def _new_dependency_record(*, dependency_id: str, dependent_topic_id: str,
    prerequisite_topic_id: str, requirement_kind: str, requirement_summary: str,
    reason: dict[str, Any],
) -> dict[str, Any]:
    return {"dependency_id": dependency_id, "record_revision": 1,
        "dependent_topic_id": dependent_topic_id,
        "prerequisite_topic_id": prerequisite_topic_id,
        "requirement_kind": requirement_kind,
        "requirement_summary": requirement_summary, "relation_state": "active",
        "gate_state": "closed", "accepted_basis_json": None,
        "gate_reason_json": _canonical_json(reason)}


def _validate_dependency_records(records: dict[str, list[dict[str, Any]]]) -> None:
    validate_dependency_records(records, ProtocolError)


def derived_gate(records: dict[str, list[dict[str, Any]]], topic_id: str) -> str:
    _validate_dependency_records(records)
    return "closed" if any(
        item["dependent_topic_id"] == topic_id
        and item["relation_state"] == "active"
        and item["gate_state"] == "closed"
        for item in records["Topic Dependencies"]
    ) else "open"


def reclose_directly_affected(
    records: dict[str, list[dict[str, Any]]], *, prerequisite_topic_id: str,
    changed_decision_ids: set[str] | None, cause: dict[str, Any], ledger_revision: int,
    invalidated_authority_ids: set[str] | None = None,
) -> list[str]:
    """Fail closed for directly affected Phase-0/1 bases only."""
    closed: list[str] = []
    for dependency in records["Topic Dependencies"]:
        if (dependency["relation_state"] != "active"
            or dependency["gate_state"] != "open"
            or dependency["prerequisite_topic_id"] != prerequisite_topic_id):
            continue
        dependent = _record_by_id(
            records["Current Topics"], "topic_id", dependency["dependent_topic_id"],
            "dependent_topic_id",
        )
        if dependent.get("current_phase") not in {0, 1}:
            continue
        basis = (canonical_object(dependency["accepted_basis_json"],
            "topic dependency accepted_basis_json", _state_corrupt)
            if dependency["accepted_basis_json"] else None)
        authorities = basis.get("decision_authority") if basis else None
        known = (isinstance(authorities, list) and bool(authorities)
            and all(isinstance(item, dict) and isinstance(item.get("decision_id"), str)
                for item in authorities))
        basis_ids = {item["decision_id"] for item in authorities} if known else set()
        authority = basis.get("authority", {}) if basis else {}
        authority_id = authority.get("result_id") or authority.get("checkpoint_id")
        if (changed_decision_ids is None or not known
            or basis_ids & changed_decision_ids
            or (invalidated_authority_ids is not None
                and authority_id in invalidated_authority_ids)):
            dependency["gate_state"] = "closed"
            dependency["record_revision"] += 1
            dependency["gate_reason_json"] = _canonical_json({
                **cause, "kind": "direct-upstream-invalidation",
                "ledger_revision": ledger_revision,
            })
            closed.append(dependency["dependency_id"])
    return sorted(closed)


def reclose_stale_topic_gates(
    records: dict[str, list[dict[str, Any]]], *, dependent_topic_id: str,
    cause: dict[str, Any], ledger_revision: int,
) -> list[str]:
    """Reclose this topic's open gates when their frozen authority is stale."""
    reclosed: list[str] = []
    for dependency in records["Topic Dependencies"]:
        if (
            dependency.get("dependent_topic_id") != dependent_topic_id
            or dependency.get("relation_state") != "active"
            or dependency.get("gate_state") != "open"
        ):
            continue
        basis = canonical_object(
            dependency["accepted_basis_json"],
            "topic dependency accepted_basis_json", _state_corrupt,
        )
        try:
            descriptor = authority_descriptor(dependency["requirement_kind"])
            selection = descriptor.selection_from_basis(basis)
            _, chosen = normalize_authority_selection(
                authority_candidates(records, dependency),
                dependency["requirement_kind"], selection.get("authority_id"),
                selection["decision_ids"],
            )
            if chosen != basis.get("decision_authority"):
                raise ProtocolError(
                    "topic_dependency_evidence_unavailable",
                    "selected dependency evidence is not current",
                )
        except ProtocolError as error:
            if error.code != "topic_dependency_evidence_unavailable":
                raise
            dependency["gate_state"] = "closed"
            dependency["record_revision"] += 1
            dependency["gate_reason_json"] = _canonical_json({
                **cause, "kind": "direct-upstream-invalidation",
                "ledger_revision": ledger_revision,
            })
            reclosed.append(dependency["dependency_id"])
    return sorted(reclosed)


def request_context(request: dict[str, Any], extra: set[str], *, query: bool = False):
    required = {"protocol_version", "operation", "project_path", "project_id",
        "tree_id", "actor_topic_id", "actor_conversation_ref"} | extra
    if not query:
        required |= {"expected_ledger_revision", "expected_topic_revision", "idempotency_key"}
    _expect_keys(request, required, f"{request['operation']} request")
    if not query:
        _validate_uuid4(request["idempotency_key"], "idempotency_key")
    return _evolution_paths(request, query=query, allow_tree_topic=True)


def _dependency_id(key: str) -> str:
    return f"DEP-{uuid.UUID(key).hex}"


def _validate_new_edge(records: dict[str, list[dict[str, Any]]], dependent: str,
    prerequisite: str, kind: Any, summary: Any, *, replacing: str | None = None,
) -> None:
    if not isinstance(dependent, str) or not isinstance(prerequisite, str):
        raise ProtocolError("invalid_request", "topic dependency endpoints are invalid")
    if dependent == prerequisite:
        raise ProtocolError("topic_dependency_state_conflict",
            "a topic cannot depend on itself", context=dependency_context(
                dependent_topic_id=dependent, prerequisite_topic_id=prerequisite))
    _record_by_id(records["Current Topics"], "topic_id", prerequisite,
        "prerequisite_topic_id")
    topic = _record_by_id(records["Current Topics"], "topic_id", dependent,
        "dependent_topic_id")
    if topic.get("current_phase") not in {0, 1}:
        raise ProtocolError("topic_dependency_phase_conflict",
            "topic dependencies are mutable only in Phase 0 or 1",
            context=dependency_context(dependent_topic_id=dependent,
                prerequisite_topic_id=prerequisite, phase=topic.get("current_phase")))
    if (not isinstance(kind, str) or kind not in DEPENDENCY_AUTHORITY_KINDS
        or not isinstance(summary, str) or not summary
        or len(summary.encode("utf-8")) > 4096):
        raise ProtocolError("invalid_request", "topic dependency requirement is invalid")
    copied = [dict(item) for item in records["Topic Dependencies"]
        if item["dependency_id"] != replacing]
    if sum(item["dependent_topic_id"] == dependent
           and item["relation_state"] == "active" for item in copied) >= 64:
        raise ProtocolError("invalid_request",
            "a topic may have at most 64 active dependencies")
    copied.append(_new_dependency_record(dependency_id="DEP-" + "f" * 32,
        dependent_topic_id=dependent, prerequisite_topic_id=prerequisite,
        requirement_kind=kind, requirement_summary=summary,
        reason={"kind": "explicit-create",
            "dependency_update_id": "00000000-0000-4000-8000-000000000000",
            "ledger_revision": 1}))
    try:
        _validate_dependency_records({**records, "Topic Dependencies": copied})
    except ProtocolError as error:
        if "cycle" in error.message:
            raise ProtocolError("topic_dependency_cycle",
                "active dependency graph would contain a cycle",
                context=dependency_context(dependent_topic_id=dependent,
                    prerequisite_topic_id=prerequisite)) from error
        if "duplicated" in error.message:
            raise ProtocolError("topic_dependency_duplicate",
                "active dependency endpoint pair is duplicated",
                context=dependency_context(dependent_topic_id=dependent,
                    prerequisite_topic_id=prerequisite)) from error
        raise


def _reject_published_authority_change(
    records: dict[str, list[dict[str, Any]]], dependent_topic_id: str,
) -> None:
    """Keep a currently published Phase-0/1 authority coherent with its gates."""
    topic = _record_by_id(records["Current Topics"], "topic_id", dependent_topic_id,
        "dependent_topic_id")
    phase = topic.get("current_phase")
    authority = (
        published_stage_entry_authority(records, dependent_topic_id, phase)
        if phase in {0, 1} else None
    )
    if authority is not None:
        raise ProtocolError(
            "topic_dependency_published_authority_conflict",
            "published authority requires explicit impact or reopen before changing dependencies",
            context=dependency_context(
                dependent_topic_id=dependent_topic_id, phase=topic.get("current_phase"),
            ) | authority,
        )


def prepare_initial_dependencies(records: dict[str, list[dict[str, Any]]], *,
    request: dict[str, Any], target_topic_id: str, handoff_id: str,
    ledger_revision: int,
) -> list[dict[str, Any]]:
    raw = request.get("initial_dependencies", [])
    if not isinstance(raw, list) or len(raw) > 64:
        raise ProtocolError("invalid_request", "initial_dependencies must be a bounded array")
    created: list[dict[str, Any]] = []
    for index, item in enumerate(raw):
        if not isinstance(item, dict):
            raise ProtocolError("invalid_request", f"initial_dependencies[{index}] must be an object")
        _expect_keys(item, {"dependent_endpoint", "prerequisite_topic_ref",
            "requirement_kind", "requirement_summary"}, "initial dependency")
        endpoint = item["dependent_endpoint"]
        prerequisite_ref = item["prerequisite_topic_ref"]
        if (
            not isinstance(endpoint, str) or not endpoint
            or not isinstance(prerequisite_ref, str) or not prerequisite_ref
            or len(endpoint.encode("utf-8")) > 4096
            or len(prerequisite_ref.encode("utf-8")) > 4096
        ):
            raise ProtocolError("invalid_request", "initial dependency endpoint is invalid")
        endpoints = {"source": request["actor_topic_id"], "target": target_topic_id}
        dependent = endpoints.get(endpoint)
        prerequisite = endpoints.get(prerequisite_ref, prerequisite_ref)
        if dependent is None:
            raise ProtocolError("invalid_request", "initial dependency endpoint is invalid")
        if dependent == request["actor_topic_id"]:
            _reject_published_authority_change(records, dependent)
        _validate_new_edge(records, dependent, prerequisite, item["requirement_kind"],
            item["requirement_summary"])
        seed = _sha256(_canonical_json({"handoff_id": handoff_id, "index": index}).encode("utf-8"))[:32]
        record = _new_dependency_record(dependency_id=f"DEP-{seed}",
            dependent_topic_id=dependent, prerequisite_topic_id=prerequisite,
            requirement_kind=item["requirement_kind"],
            requirement_summary=item["requirement_summary"],
            reason={"kind": "initial-handoff", "handoff_id": handoff_id,
                "ledger_revision": ledger_revision})
        records["Topic Dependencies"].append(record)
        created.append({key: record[key] for key in ("dependency_id", "record_revision",
            "dependent_topic_id", "prerequisite_topic_id", "requirement_kind",
            "requirement_summary", "gate_state")})
    return sorted(created, key=lambda item: item["dependency_id"])


def update_topic_dependency(request: dict[str, Any]) -> dict[str, Any]:
    action = request.get("action")
    if not isinstance(action, str) or action not in {"create", "replace", "cancel"}:
        raise ProtocolError("invalid_request", "dependency action is unsupported")
    fields = {
        "create": {"action", "prerequisite_topic_id", "requirement_kind", "requirement_summary"},
        "replace": {"action", "dependency_id", "expected_dependency_revision",
            "prerequisite_topic_id", "requirement_kind", "requirement_summary"},
        "cancel": {"action", "dependency_id", "expected_dependency_revision"},
    }
    _, ledger_path, _, lock_path, owner_ref = request_context(request, fields[action])
    with lock_path.open("a+b") as stream:
        _flock_with_timeout(stream)
        frontmatter, records = _load_records(ledger_path)
        replay = _idempotent_result(records, request)
        if replay is not None:
            return replay
        topic = _record_by_id(records["Current Topics"], "topic_id",
            request["actor_topic_id"], "topic_id")
        revision, topic_revision = _validate_revisions(request, frontmatter, topic)
        _verify_topic_owner(records, request["actor_topic_id"], owner_ref)
        if topic["current_phase"] not in {0, 1}:
            raise ProtocolError("topic_dependency_phase_conflict",
                "topic dependencies are immutable after Phase 1",
                context=dependency_context(dependent_topic_id=request["actor_topic_id"],
                    phase=topic["current_phase"]))
        if action == "create":
            _reject_published_authority_change(records, request["actor_topic_id"])
            _validate_new_edge(records, request["actor_topic_id"],
                request["prerequisite_topic_id"], request["requirement_kind"],
                request["requirement_summary"])
            dep = _new_dependency_record(dependency_id=_dependency_id(request["idempotency_key"]),
                dependent_topic_id=request["actor_topic_id"],
                prerequisite_topic_id=request["prerequisite_topic_id"],
                requirement_kind=request["requirement_kind"],
                requirement_summary=request["requirement_summary"],
                reason={"kind": "explicit-create",
                    "dependency_update_id": request["idempotency_key"],
                    "ledger_revision": revision + 1})
            records["Topic Dependencies"].append(dep)
        else:
            dep = _record_by_id(records["Topic Dependencies"], "dependency_id",
                request["dependency_id"], "dependency_id")
            if dep["dependent_topic_id"] != request["actor_topic_id"]:
                raise ProtocolError("topic_dependency_ownership_conflict",
                    "only the dependent topic may mutate a dependency",
                    context=dependency_context(dependency_id=dep["dependency_id"],
                        dependent_topic_id=dep["dependent_topic_id"],
                        prerequisite_topic_id=dep["prerequisite_topic_id"]))
            if request["expected_dependency_revision"] != dep["record_revision"]:
                raise ProtocolError("record_revision_conflict", "dependency revision is stale",
                    context=dependency_context(dependency_id=dep["dependency_id"],
                        dependent_topic_id=dep["dependent_topic_id"],
                        prerequisite_topic_id=dep["prerequisite_topic_id"],
                        expected_dependency_revision=request["expected_dependency_revision"],
                        actual_dependency_revision=dep["record_revision"]))
            if action == "replace":
                _reject_published_authority_change(records, request["actor_topic_id"])
                _validate_new_edge(records, request["actor_topic_id"],
                    request["prerequisite_topic_id"], request["requirement_kind"],
                    request["requirement_summary"], replacing=dep["dependency_id"])
                dep.update({"prerequisite_topic_id": request["prerequisite_topic_id"],
                    "requirement_kind": request["requirement_kind"],
                    "requirement_summary": request["requirement_summary"],
                    "relation_state": "active", "gate_state": "closed"})
            else:
                dep["relation_state"] = "cancelled"
            dep["record_revision"] += 1
            dep["gate_reason_json"] = _canonical_json({"kind": f"explicit-{action}",
                "dependency_update_id": request["idempotency_key"],
                "ledger_revision": revision + 1})
        result = {"ok": True, "state": action, "idempotent_replay": False,
            "dependency_id": dep["dependency_id"],
            "dependency_revision": dep["record_revision"],
            "ledger_revision": revision + 1, "record_revision": topic_revision,
            "derived_gate_state": derived_gate(records, request["actor_topic_id"])}
        _inject_failure("topic-dependency-before-ledger-write")
        _write_ledger_transaction(ledger_path, frontmatter, records, request,
            ledger_revision=revision + 1, event_type=f"topic-dependency-{action}",
            result=result)
        return result
