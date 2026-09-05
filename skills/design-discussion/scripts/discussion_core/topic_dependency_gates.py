"""Derived gate evaluation, release, and Phase-0/1 guard operations."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from .state import (
    ProtocolError, SHA256_RE, _canonical_json, _flock_with_timeout,
    _idempotent_result, _load_records, _record_by_id, _sha256,
    _validate_revisions, _verify_topic_owner, _write_ledger_transaction,
)
from .topic_dependency_schema import (
    DEPENDENCY_AUTHORITY_KINDS, authority_descriptor, canonical_object,
    canonical_string_array,
)
from .topic_dependency_authority import (
    authority_candidates, normalize_authority_selection,
)
from .topic_dependency_lifecycle import (
    dependency_context, derived_gate, reclose_directly_affected, request_context,
)


@dataclass(frozen=True)
class GateOperationPolicy:
    """Declarative boundary for operations governed by Phase-0/1 dependencies."""

    gate_phases: frozenset[int] = frozenset()
    reclose_directly_affected: bool = False


GATE_OPERATION_POLICIES = {
    "discussion-update": GateOperationPolicy(frozenset({0, 1})),
    "stage-entry-checkpoint": GateOperationPolicy(frozenset({0, 1})),
    "prepare-handoff": GateOperationPolicy(frozenset({0, 1})),
    "authorize-handoff-discussion": GateOperationPolicy(frozenset({0, 1})),
    "phase-transition": GateOperationPolicy(frozenset({0, 1})),
    "decision-impact": GateOperationPolicy(reclose_directly_affected=True),
    "checkpoint-broken": GateOperationPolicy(reclose_directly_affected=True),
    "phase-reopen": GateOperationPolicy(reclose_directly_affected=True),
}


def apply_gate_policy(
    records: dict[str, list[dict[str, Any]]], operation: str, topic_id: str,
    *, phase: Any | None = None, reclose: dict[str, Any] | None = None,
) -> list[str]:
    """Enforce or reclose through the one declared operation policy.

    The boundary keeps all public operations on the same Phase-0/1 rule while
    leaving Phase 2+ and transitions already outside that domain untouched.
    """
    policy = GATE_OPERATION_POLICIES.get(operation)
    if policy is None:
        raise ValueError(f"unknown topic dependency gate operation: {operation}")
    if policy.gate_phases:
        subject_phase = phase
        if subject_phase is None:
            subject_phase = _record_by_id(
                records["Current Topics"], "topic_id", topic_id, "topic_id"
            ).get("current_phase")
        if subject_phase in policy.gate_phases:
            require_open_gate(records, topic_id)
    if not policy.reclose_directly_affected:
        return []
    if not isinstance(reclose, dict):
        raise ValueError(f"{operation} requires direct invalidation details")
    return reclose_directly_affected(
        records, prerequisite_topic_id=topic_id, **reclose
    )

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


def _closed(records: dict[str, list[dict[str, Any]]], topic_id: str) -> list[dict[str, Any]]:
    return sorted((item for item in records["Topic Dependencies"] if item["dependent_topic_id"] == topic_id and item["relation_state"] == "active" and item["gate_state"] == "closed"), key=lambda item: item["dependency_id"])


def _pending_child_result_impacts(
    records: dict[str, list[dict[str, Any]]], dependency: dict[str, Any],
) -> list[str]:
    """Find unresolved child impacts that must keep this exact edge closed."""
    pending: list[str] = []

    def corrupt(code: str, message: str) -> None:
        raise ProtocolError(code, message)

    for record in records["Impacts"]:
        impact = canonical_object(record.get("data_json"), "impact data_json", corrupt)
        if (
            impact.get("state") == "pending"
            and impact.get("source_topic_id") == dependency["prerequisite_topic_id"]
            and impact.get("target_topic_id") == dependency["dependent_topic_id"]
            and isinstance(impact.get("handoff_id"), str)
            and isinstance(impact.get("impact_id"), str)
        ):
            pending.append(impact["impact_id"])
    return sorted(pending)


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
                or not canonical_string_array(item.get("decision_ids"))
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
        pending_impacts = _pending_child_result_impacts(records, dependency)
        candidates = [] if pending_impacts else authority_candidates(records, dependency)
        selected = choices.get(dependency["dependency_id"])
        detail: dict[str, Any] = {"dependency_id": dependency["dependency_id"], "record_revision": dependency["record_revision"], "requirement_kind": dependency["requirement_kind"], "requirement_summary": dependency["requirement_summary"], "prerequisite_topic_id": dependency["prerequisite_topic_id"], "prerequisite_phase": prerequisite_topic["current_phase"], "prerequisite_state": prerequisite_topic["topic_state"], "candidates": candidates}
        if pending_impacts:
            detail["pending_impact_ids"] = pending_impacts
            detail["waiting_reason"] = "matching child-result impact is pending"
            details.append(detail)
            continue
        if selected is not None:
            authority_id = selected.get("authority_id")
            descriptor = authority_descriptor(dependency["requirement_kind"])
            expected_selection_fields = {"dependency_id", "decision_ids"}
            if descriptor.identity_field is not None:
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
                        context=dependency_context(
                            dependency_id=dependency["dependency_id"],
                            dependent_topic_id=dependency["dependent_topic_id"],
                            prerequisite_topic_id=dependency["prerequisite_topic_id"],
                        ),
                    ) from error
                raise
            basis = descriptor.basis_from_candidate(
                candidate,
                dependency_id=dependency["dependency_id"],
                prerequisite_topic_id=dependency["prerequisite_topic_id"],
                decision_authority=[
                    {"decision_id": item["decision_id"], "sha256": item["sha256"]}
                    for item in chosen
                ],
            )
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


def evaluate_topic_gate(request: dict[str, Any]) -> dict[str, Any]:
    _, ledger_path, _, lock_path, owner_ref = request_context(request, {"basis_selection"} if "basis_selection" in request else set(), query=True)
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
    if not isinstance(kind, str) or kind not in DEPENDENCY_AUTHORITY_KINDS:
        return {"dependency_id": item.get("dependency_id"), "decision_ids": []}
    selection = authority_descriptor(kind).selection_from_basis(basis)
    selection["dependency_id"] = item["dependency_id"]
    return selection


def release_topic_gate(request: dict[str, Any]) -> dict[str, Any]:
    digest = request.get("release_set_sha256")
    if not isinstance(digest, str) or not SHA256_RE.fullmatch(digest):
        raise ProtocolError("invalid_request", "release_set_sha256 must be a SHA-256 digest")
    _, ledger_path, _, lock_path, owner_ref = request_context(request, {"release_set", "release_set_sha256"})
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
                context=dependency_context(
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
