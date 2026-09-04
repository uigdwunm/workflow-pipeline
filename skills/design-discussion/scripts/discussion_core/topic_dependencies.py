"""Topic-owned Phase-0/1 requirement gates.

This module is deliberately the only place that understands dependency records.
Callers supply an already authenticated discussion request; they never infer a
gate from parenthood, result absorption, or delivery coordination.
"""

from __future__ import annotations

import uuid
from typing import Any

from .state import (
    ProtocolError, SHA256_RE, _canonical_json, _evolution_paths, _expect_keys,
    _flock_with_timeout, _idempotent_result, _inject_failure, _load_records,
    _record_by_id, _sha256, _validate_revisions, _validate_uuid4,
    _verify_topic_owner,
    _write_ledger_transaction,
)
from .topic_dependency_schema import (
    DEPENDENCY_AUTHORITY_KINDS, canonical_object as _canonical_object,
    canonical_string_array as _canonical_string_array, validate_dependency_records,
)
from .topic_dependency_authority import (
    authority_candidates, authority_handler,
    normalize_authority_selection,
    freeze_authority_selection, has_current_authority,
    release_child_result_dependencies, retained_checkpoint_identities,
)



def _new_dependency_record(*, dependency_id: str, dependent_topic_id: str, prerequisite_topic_id: str, requirement_kind: str, requirement_summary: str, reason: dict[str, Any]) -> dict[str, Any]:
    return {"dependency_id": dependency_id, "record_revision": 1, "dependent_topic_id": dependent_topic_id, "prerequisite_topic_id": prerequisite_topic_id, "requirement_kind": requirement_kind, "requirement_summary": requirement_summary, "relation_state": "active", "gate_state": "closed", "accepted_basis_json": None, "gate_reason_json": _canonical_json(reason)}


def _state_corrupt(code: str, message: str) -> None:
    raise ProtocolError(code, message)


def _invalid_request(code: str, message: str) -> None:
    raise ProtocolError(code, message)


def _validate_dependency_records(records: dict[str, list[dict[str, Any]]]) -> None:
    validate_dependency_records(records, ProtocolError)


def derived_gate(records: dict[str, list[dict[str, Any]]], topic_id: str) -> str:
    _validate_dependency_records(records)
    return "closed" if any(
        item["dependent_topic_id"] == topic_id and item["relation_state"] == "active"
        and item["gate_state"] == "closed" for item in records["Topic Dependencies"]
    ) else "open"


def require_open_gate(records: dict[str, list[dict[str, Any]]], topic_id: str) -> None:
    topic = _record_by_id(records["Current Topics"], "topic_id", topic_id, "topic_id")
    if topic.get("current_phase") in {0, 1} and derived_gate(records, topic_id) == "closed":
        blocked_dependencies = []
        for dependency in records["Topic Dependencies"]:
            if (
                dependency["dependent_topic_id"] != topic_id
                or dependency["relation_state"] != "active"
                or dependency["gate_state"] != "closed"
            ):
                continue
            prerequisite = _record_by_id(
                records["Current Topics"],
                "topic_id",
                dependency["prerequisite_topic_id"],
                "prerequisite_topic_id",
            )
            blocked_dependencies.append(
                {
                    "dependency_id": dependency["dependency_id"],
                    "prerequisite_topic_id": prerequisite["topic_id"],
                    "prerequisite_phase": prerequisite["current_phase"],
                    "prerequisite_state": prerequisite["topic_state"],
                    "waiting_reason": "current required authority is unavailable",
                }
            )
        raise ProtocolError(
            "topic_gate_closed",
            "topic has an active closed requirements dependency",
            context={
                "topic_id": topic_id,
                "derived_gate_state": "closed",
                "blocked_dependencies": sorted(
                    blocked_dependencies, key=lambda item: item["dependency_id"]
                ),
            },
        )


def require_open_gate_for_phase_transition(
    records: dict[str, list[dict[str, Any]]], topic_id: str, from_phase: Any,
) -> None:
    """Apply the requirements-gate boundary to a Phase-0/1 transition point.

    Phase Run owners call this instead of deciding locally which transitions are
    gate-bound.  Phase 2+ deliberately remains outside this requirements-only
    domain, including recovery for an already active run.
    """
    if from_phase in {0, 1}:
        require_open_gate(records, topic_id)


def reclose_directly_affected(
    records: dict[str, list[dict[str, Any]]], *, prerequisite_topic_id: str,
    changed_decision_ids: set[str] | None, cause: dict[str, Any], ledger_revision: int,
    invalidated_authority_ids: set[str] | None = None,
) -> list[str]:
    """Fail closed for direct bases with unknown or intersecting provenance only."""
    closed: list[str] = []
    for dependency in records["Topic Dependencies"]:
        if dependency["relation_state"] != "active" or dependency["gate_state"] != "open" or dependency["prerequisite_topic_id"] != prerequisite_topic_id:
            continue
        dependent = _record_by_id(records["Current Topics"], "topic_id", dependency["dependent_topic_id"], "dependent_topic_id")
        if dependent.get("current_phase") not in {0, 1}:
            continue
        basis = _canonical_object(dependency["accepted_basis_json"], "topic dependency accepted_basis_json", _state_corrupt) if dependency["accepted_basis_json"] else None
        authorities = basis.get("decision_authority") if basis else None
        known = isinstance(authorities, list) and bool(authorities) and all(isinstance(item, dict) and isinstance(item.get("decision_id"), str) for item in authorities)
        basis_ids = {item["decision_id"] for item in authorities} if known else set()
        authority = basis.get("authority", {}) if basis else {}
        authority_id = authority.get("result_id") or authority.get("checkpoint_id")
        if changed_decision_ids is None or not known or basis_ids & changed_decision_ids or (invalidated_authority_ids is not None and authority_id in invalidated_authority_ids):
            dependency["gate_state"] = "closed"
            dependency["record_revision"] += 1
            dependency["gate_reason_json"] = _canonical_json({**cause, "kind": "direct-upstream-invalidation", "ledger_revision": ledger_revision})
            closed.append(dependency["dependency_id"])
    return sorted(closed)


def _closed(records: dict[str, list[dict[str, Any]]], topic_id: str) -> list[dict[str, Any]]:
    return sorted((item for item in records["Topic Dependencies"] if item["dependent_topic_id"] == topic_id and item["relation_state"] == "active" and item["gate_state"] == "closed"), key=lambda item: item["dependency_id"])


def _evaluation(records: dict[str, list[dict[str, Any]]], topic_id: str, selection: list[dict[str, Any]] | None) -> dict[str, Any]:
    closed = _closed(records, topic_id)
    if not closed:
        return {"state": "open", "derived_gate_state": "open", "dependencies": []}
    if selection is not None:
        if (
            not isinstance(selection, list)
            or len(selection) > 64
            or any(
                not isinstance(item, dict)
                or not set(item).issubset({"dependency_id", "decision_ids", "authority_id"})
                or not isinstance(item.get("dependency_id"), str)
                or not _canonical_string_array(item.get("decision_ids"))
                for item in selection
            )
        ):
            raise ProtocolError("invalid_request", "basis_selection is invalid")
        selected_ids = [item["dependency_id"] for item in selection]
        closed_ids = [item["dependency_id"] for item in closed]
        if selected_ids != closed_ids:
            raise ProtocolError("invalid_request", "basis_selection must name active closed dependencies exactly once in canonical order")
    choices = {item["dependency_id"]: item for item in selection or []}
    details = []
    proposed = []
    for dependency in closed:
        prerequisite_topic = _record_by_id(records["Current Topics"], "topic_id", dependency["prerequisite_topic_id"], "prerequisite_topic_id")
        candidates = authority_candidates(records, dependency)
        selected = choices.get(dependency["dependency_id"])
        detail: dict[str, Any] = {"dependency_id": dependency["dependency_id"], "record_revision": dependency["record_revision"], "requirement_kind": dependency["requirement_kind"], "requirement_summary": dependency["requirement_summary"], "prerequisite_topic_id": dependency["prerequisite_topic_id"], "prerequisite_phase": prerequisite_topic["current_phase"], "prerequisite_state": prerequisite_topic["topic_state"], "candidates": candidates}
        if selected is not None:
            authority_id = selected.get("authority_id")
            handler = authority_handler(dependency["requirement_kind"])
            expected_selection_fields = {"dependency_id", "decision_ids"}
            if handler["identity_field"] is not None:
                expected_selection_fields.add("authority_id")
            if set(selected) != expected_selection_fields:
                raise ProtocolError("topic_dependency_evidence_unavailable", "selected dependency evidence is not current")
            try:
                candidate, chosen = normalize_authority_selection(
                    candidates, dependency["requirement_kind"], authority_id,
                    selected.get("decision_ids"),
                )
            except ProtocolError as error:
                if error.code == "topic_dependency_evidence_unavailable":
                    raise ProtocolError(
                        error.code, error.message,
                        context=_dependency_context(
                            dependency_id=dependency["dependency_id"],
                            dependent_topic_id=dependency["dependent_topic_id"],
                            prerequisite_topic_id=dependency["prerequisite_topic_id"],
                        ),
                    ) from error
                raise
            basis = {"basis_version": 1, "dependency_id": dependency["dependency_id"], "prerequisite_topic_id": dependency["prerequisite_topic_id"], "requirement_kind": dependency["requirement_kind"], "decision_authority": [{"decision_id": item["decision_id"], "sha256": item["sha256"]} for item in chosen]}
            if candidate.get("authority") is not None:
                basis["authority"] = candidate["authority"]
            detail["proposed_basis"] = basis
            proposed.append({"dependency_id": dependency["dependency_id"], "record_revision": dependency["record_revision"], "basis": basis})
        elif not candidates:
            detail["waiting_reason"] = "current required authority is unavailable"
        details.append(detail)
    releasable = all(item["candidates"] for item in details)
    result: dict[str, Any] = {"state": "releasable" if releasable else "blocked", "derived_gate_state": "closed", "dependencies": details}
    if releasable and selection is not None:
        release_set = sorted(proposed, key=lambda item: item["dependency_id"])
        result["release_set"] = release_set
    return result


def _request_context(request: dict[str, Any], extra: set[str], *, query: bool = False):
    required = {"protocol_version", "operation", "project_path", "project_id", "tree_id", "actor_topic_id", "actor_conversation_ref"} | extra
    if not query:
        required |= {"expected_ledger_revision", "expected_topic_revision", "idempotency_key"}
    _expect_keys(request, required, f"{request['operation']} request")
    if not query:
        _validate_uuid4(request["idempotency_key"], "idempotency_key")
    return _evolution_paths(request, query=query, allow_tree_topic=True)


def _dependency_id(key: str) -> str:
    return f"DEP-{uuid.UUID(key).hex}"


def _dependency_context(
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


def _validate_new_edge(records: dict[str, list[dict[str, Any]]], dependent: str, prerequisite: str, kind: Any, summary: Any, *, replacing: str | None = None) -> None:
    if not isinstance(dependent, str) or not isinstance(prerequisite, str):
        raise ProtocolError("invalid_request", "topic dependency endpoints are invalid")
    if dependent == prerequisite:
        raise ProtocolError("topic_dependency_state_conflict", "a topic cannot depend on itself", context=_dependency_context(dependent_topic_id=dependent, prerequisite_topic_id=prerequisite))
    _record_by_id(records["Current Topics"], "topic_id", prerequisite, "prerequisite_topic_id")
    topic = _record_by_id(records["Current Topics"], "topic_id", dependent, "dependent_topic_id")
    if topic.get("current_phase") not in {0, 1}:
        raise ProtocolError("topic_dependency_phase_conflict", "topic dependencies are mutable only in Phase 0 or 1", context=_dependency_context(dependent_topic_id=dependent, prerequisite_topic_id=prerequisite, phase=topic.get("current_phase")))
    if not isinstance(kind, str) or kind not in DEPENDENCY_AUTHORITY_KINDS or not isinstance(summary, str) or not summary or len(summary.encode("utf-8")) > 4096:
        raise ProtocolError("invalid_request", "topic dependency requirement is invalid")
    copied = [dict(item) for item in records["Topic Dependencies"] if item["dependency_id"] != replacing]
    if sum(
        item["dependent_topic_id"] == dependent
        and item["relation_state"] == "active"
        # Capacity is about mutable active edges, not their momentary gate
        # state: an open edge can be directly reclosed later.
        for item in copied
    ) >= 64:
        raise ProtocolError("invalid_request", "a topic may have at most 64 active dependencies")
    copied.append(_new_dependency_record(dependency_id="DEP-" + "f" * 32, dependent_topic_id=dependent, prerequisite_topic_id=prerequisite, requirement_kind=kind, requirement_summary=summary, reason={"kind": "explicit-create", "dependency_update_id": "00000000-0000-4000-8000-000000000000", "ledger_revision": 1}))
    try:
        _validate_dependency_records({**records, "Topic Dependencies": copied})
    except ProtocolError as error:
        if "cycle" in error.message:
            raise ProtocolError("topic_dependency_cycle", "active dependency graph would contain a cycle", context=_dependency_context(dependent_topic_id=dependent, prerequisite_topic_id=prerequisite)) from error
        if "duplicated" in error.message:
            raise ProtocolError("topic_dependency_duplicate", "active dependency endpoint pair is duplicated", context=_dependency_context(dependent_topic_id=dependent, prerequisite_topic_id=prerequisite)) from error
        raise


def prepare_initial_dependencies(
    records: dict[str, list[dict[str, Any]]], *, request: dict[str, Any], target_topic_id: str,
    handoff_id: str, ledger_revision: int,
) -> list[dict[str, Any]]:
    """Resolve a bounded handoff dependency declaration after child identity exists."""
    raw = request.get("initial_dependencies", [])
    if not isinstance(raw, list) or len(raw) > 64:
        raise ProtocolError("invalid_request", "initial_dependencies must be a bounded array")
    created: list[dict[str, Any]] = []
    for index, item in enumerate(raw):
        if not isinstance(item, dict):
            raise ProtocolError("invalid_request", f"initial_dependencies[{index}] must be an object")
        _expect_keys(item, {"dependent_endpoint", "prerequisite_topic_ref", "requirement_kind", "requirement_summary"}, "initial dependency")
        endpoints = {"source": request["actor_topic_id"], "target": target_topic_id}
        dependent = endpoints.get(item["dependent_endpoint"])
        prerequisite = endpoints.get(item["prerequisite_topic_ref"], item["prerequisite_topic_ref"])
        if dependent is None or not isinstance(prerequisite, str):
            raise ProtocolError("invalid_request", "initial dependency endpoint is invalid")
        _validate_new_edge(records, dependent, prerequisite, item["requirement_kind"], item["requirement_summary"])
        seed = _sha256(_canonical_json({"handoff_id": handoff_id, "index": index}).encode("utf-8"))[:32]
        record = _new_dependency_record(dependency_id=f"DEP-{seed}", dependent_topic_id=dependent, prerequisite_topic_id=prerequisite, requirement_kind=item["requirement_kind"], requirement_summary=item["requirement_summary"], reason={"kind": "initial-handoff", "handoff_id": handoff_id, "ledger_revision": ledger_revision})
        records["Topic Dependencies"].append(record)
        created.append({key: record[key] for key in ("dependency_id", "record_revision", "dependent_topic_id", "prerequisite_topic_id", "requirement_kind", "requirement_summary", "gate_state")})
    return sorted(created, key=lambda item: item["dependency_id"])


def update_topic_dependency(request: dict[str, Any]) -> dict[str, Any]:
    action = request.get("action")
    if not isinstance(action, str) or action not in {"create", "replace", "cancel"}:
        raise ProtocolError("invalid_request", "dependency action is unsupported")
    fields = {
        "create": {"action", "prerequisite_topic_id", "requirement_kind", "requirement_summary"},
        "replace": {"action", "dependency_id", "expected_dependency_revision", "prerequisite_topic_id", "requirement_kind", "requirement_summary"},
        "cancel": {"action", "dependency_id", "expected_dependency_revision"},
    }
    _, ledger_path, _, lock_path, owner_ref = _request_context(request, fields[action])
    with lock_path.open("a+b") as stream:
        _flock_with_timeout(stream)
        frontmatter, records = _load_records(ledger_path)
        replay = _idempotent_result(records, request)
        if replay is not None: return replay
        topic = _record_by_id(records["Current Topics"], "topic_id", request["actor_topic_id"], "topic_id")
        revision, topic_revision = _validate_revisions(request, frontmatter, topic)
        _verify_topic_owner(records, request["actor_topic_id"], owner_ref)
        if topic["current_phase"] not in {0, 1}: raise ProtocolError("topic_dependency_phase_conflict", "topic dependencies are immutable after Phase 1", context=_dependency_context(dependent_topic_id=request["actor_topic_id"], phase=topic["current_phase"]))
        if action == "create":
            _validate_new_edge(records, request["actor_topic_id"], request["prerequisite_topic_id"], request["requirement_kind"], request["requirement_summary"])
            dep = _new_dependency_record(dependency_id=_dependency_id(request["idempotency_key"]), dependent_topic_id=request["actor_topic_id"], prerequisite_topic_id=request["prerequisite_topic_id"], requirement_kind=request["requirement_kind"], requirement_summary=request["requirement_summary"], reason={"kind": "explicit-create", "dependency_update_id": request["idempotency_key"], "ledger_revision": revision + 1})
            records["Topic Dependencies"].append(dep)
        else:
            dep = _record_by_id(records["Topic Dependencies"], "dependency_id", request["dependency_id"], "dependency_id")
            if dep["dependent_topic_id"] != request["actor_topic_id"]: raise ProtocolError("topic_dependency_ownership_conflict", "only the dependent topic may mutate a dependency", context=_dependency_context(dependency_id=dep["dependency_id"], dependent_topic_id=dep["dependent_topic_id"], prerequisite_topic_id=dep["prerequisite_topic_id"]))
            if request["expected_dependency_revision"] != dep["record_revision"]: raise ProtocolError("record_revision_conflict", "dependency revision is stale", context=_dependency_context(dependency_id=dep["dependency_id"], dependent_topic_id=dep["dependent_topic_id"], prerequisite_topic_id=dep["prerequisite_topic_id"], expected_dependency_revision=request["expected_dependency_revision"], actual_dependency_revision=dep["record_revision"]))
            if action == "replace":
                _validate_new_edge(records, request["actor_topic_id"], request["prerequisite_topic_id"], request["requirement_kind"], request["requirement_summary"], replacing=dep["dependency_id"])
                dep.update({"prerequisite_topic_id": request["prerequisite_topic_id"], "requirement_kind": request["requirement_kind"], "requirement_summary": request["requirement_summary"], "relation_state": "active", "gate_state": "closed"})
                # A released basis documents the prior edge.  Mark this
                # replacement provenance before derived-gate validation reads
                # the changed record in the transaction.
                dep["gate_reason_json"] = _canonical_json({"kind": "explicit-replace", "dependency_update_id": request["idempotency_key"], "ledger_revision": revision + 1})
            else:
                dep["relation_state"] = "cancelled"
            dep["record_revision"] += 1
            dep["gate_reason_json"] = _canonical_json({"kind": f"explicit-{action}", "dependency_update_id": request["idempotency_key"], "ledger_revision": revision + 1})
        result = {"ok": True, "state": action, "idempotent_replay": False, "dependency_id": dep["dependency_id"], "dependency_revision": dep["record_revision"], "ledger_revision": revision + 1, "record_revision": topic_revision, "derived_gate_state": derived_gate(records, request["actor_topic_id"])}
        _inject_failure("topic-dependency-before-ledger-write")
        _write_ledger_transaction(ledger_path, frontmatter, records, request, ledger_revision=revision + 1, event_type=f"topic-dependency-{action}", result=result)
        return result


def evaluate_topic_gate(request: dict[str, Any]) -> dict[str, Any]:
    _, ledger_path, _, lock_path, owner_ref = _request_context(request, {"basis_selection"} if "basis_selection" in request else set(), query=True)
    with lock_path.open("a+b") as stream:
        _flock_with_timeout(stream)
        frontmatter, records = _load_records(ledger_path)
        _verify_topic_owner(records, request["actor_topic_id"], owner_ref)
        topic = _record_by_id(records["Current Topics"], "topic_id", request["actor_topic_id"], "topic_id")
        selection = request.get("basis_selection")
        if "basis_selection" in request and (
            not isinstance(selection, list) or len(selection) > 64
        ):
            raise ProtocolError("invalid_request", "basis_selection is invalid")
        result = _evaluation(records, request["actor_topic_id"], selection)
        result.update({"ok": True, "ledger_revision": int(frontmatter["ledger_revision"]), "record_revision": topic["record_revision"], "topic_id": request["actor_topic_id"]})
        if "release_set" in result:
            result["release_set_sha256"] = _release_set_digest(
                result["ledger_revision"], topic["record_revision"], result["release_set"]
            )
        return result


def _release_set_digest(
    ledger_revision: int, topic_revision: int, release_set: list[dict[str, Any]]
) -> str:
    """Hash one canonical gate-release proposal at its observed revisions."""
    return _sha256(_canonical_json({
        "ledger_revision": ledger_revision,
        "topic_revision": topic_revision,
        "release_set": release_set,
    }).encode("utf-8"))


def _release_selection_from_basis(item: dict[str, Any]) -> dict[str, Any]:
    if (
        not isinstance(item, dict)
        or set(item) != {"dependency_id", "record_revision", "basis"}
        or not isinstance(item["dependency_id"], str)
        or not isinstance(item["record_revision"], int)
        or not isinstance(item["basis"], dict)
    ):
        raise ProtocolError("invalid_request", "release_set is invalid")
    basis = item.get("basis")
    if not isinstance(basis, dict):
        return {"dependency_id": item.get("dependency_id"), "decision_ids": []}
    kind = basis.get("requirement_kind")
    handler = authority_handler(kind) if isinstance(kind, str) and kind in DEPENDENCY_AUTHORITY_KINDS else None
    selection = {
        "dependency_id": item.get("dependency_id"),
        "decision_ids": [
            entry["decision_id"]
            for entry in basis.get("decision_authority", [])
            if isinstance(entry, dict) and isinstance(entry.get("decision_id"), str)
        ],
    }
    if handler is not None and handler["identity_field"] is not None:
        authority = basis.get("authority")
        selection["authority_id"] = (
            authority.get(handler["identity_field"])
            if isinstance(authority, dict)
            else None
        )
    return selection


def release_topic_gate(request: dict[str, Any]) -> dict[str, Any]:
    digest = request.get("release_set_sha256")
    if not isinstance(digest, str) or not SHA256_RE.fullmatch(digest):
        raise ProtocolError("invalid_request", "release_set_sha256 must be a SHA-256 digest")
    _, ledger_path, _, lock_path, owner_ref = _request_context(request, {"release_set", "release_set_sha256"})
    with lock_path.open("a+b") as stream:
        _flock_with_timeout(stream)
        frontmatter, records = _load_records(ledger_path)
        replay = _idempotent_result(records, request)
        if replay is not None: return replay
        topic = _record_by_id(records["Current Topics"], "topic_id", request["actor_topic_id"], "topic_id")
        try:
            revision, topic_revision = _validate_revisions(request, frontmatter, topic)
        except ProtocolError as error:
            if error.code in {"ledger_revision_conflict", "topic_revision_conflict"}:
                raise ProtocolError(
                    "topic_gate_evaluation_stale", "topic gate evaluation is stale",
                    context=error.context,
                ) from error
            raise
        _verify_topic_owner(records, request["actor_topic_id"], owner_ref)
        stale_context = {
            "ledger_revision": revision,
            "topic_revision": topic_revision,
            "dependent_topic_id": request["actor_topic_id"],
        }
        if topic["current_phase"] not in {0, 1}:
            raise ProtocolError(
                "topic_dependency_phase_conflict",
                "topic dependencies are immutable after Phase 1",
                context=_dependency_context(
                    dependent_topic_id=request["actor_topic_id"],
                    phase=topic["current_phase"],
                ),
            )
        if not isinstance(request["release_set"], list) or len(request["release_set"]) > 64:
            raise ProtocolError("invalid_request", "release_set is invalid")
        selection = [_release_selection_from_basis(item) for item in request["release_set"]]
        try:
            evaluation = _evaluation(records, request["actor_topic_id"], selection)
        except ProtocolError as error:
            if error.code == "topic_dependency_evidence_unavailable":
                raise ProtocolError(
                    "topic_gate_evaluation_stale", "topic gate evaluation is stale",
                    context=stale_context,
                ) from error
            raise
        expected = _release_set_digest(
            revision, topic_revision, evaluation.get("release_set", [])
        )
        if evaluation.get("state") != "releasable" or request["release_set"] != evaluation.get("release_set") or request["release_set_sha256"] != expected:
            raise ProtocolError(
                "topic_gate_evaluation_stale", "topic gate evaluation is stale",
                context=stale_context,
            )
        for item in _closed(records, request["actor_topic_id"]):
            match = next(entry for entry in evaluation["release_set"] if entry["dependency_id"] == item["dependency_id"])
            item["gate_state"] = "open"; item["record_revision"] += 1; item["accepted_basis_json"] = _canonical_json(match["basis"]); item["gate_reason_json"] = _canonical_json({"kind": "atomic-release", "release_id": request["idempotency_key"], "ledger_revision": revision + 1})
        result = {"ok": True, "state": "open", "idempotent_replay": False, "ledger_revision": revision + 1, "record_revision": topic_revision, "derived_gate_state": "open", "accepted_bases": evaluation["release_set"]}
        _write_ledger_transaction(ledger_path, frontmatter, records, request, ledger_revision=revision + 1, event_type="topic-gate-released", result=result)
        return result
