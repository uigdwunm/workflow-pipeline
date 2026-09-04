"""Topic-owned Phase-0/1 requirement gates.

This module is deliberately the only place that understands dependency records.
Callers supply an already authenticated discussion request; they never infer a
gate from parenthood, result absorption, or delivery coordination.
"""

from __future__ import annotations

import json
import uuid
from typing import Any

from .state import (
    ProtocolError, _canonical_json, _evolution_paths, _expect_keys,
    _expect_string, _flock_with_timeout, _idempotent_result, _json_field,
    _load_records, _record_by_id, _sha256, _topic_snapshot, _validate_revisions,
    _validate_uuid4, _validated_string_list, _verify_topic_owner,
    _write_ledger_transaction,
)
from .topic_dependency_schema import KINDS, validate_dependency_records



def _new_dependency_record(*, dependency_id: str, dependent_topic_id: str, prerequisite_topic_id: str, requirement_kind: str, requirement_summary: str, reason: dict[str, Any]) -> dict[str, Any]:
    return {"dependency_id": dependency_id, "record_revision": 1, "dependent_topic_id": dependent_topic_id, "prerequisite_topic_id": prerequisite_topic_id, "requirement_kind": requirement_kind, "requirement_summary": requirement_summary, "relation_state": "active", "gate_state": "closed", "accepted_basis_json": None, "gate_reason_json": _canonical_json(reason)}


def retained_checkpoint_identities(records: dict[str, list[dict[str, Any]]]) -> set[str]:
    """Return snapshot identities retained by active accepted dependency bases."""
    retained: set[str] = set()
    for dependency in records["Topic Dependencies"]:
        if dependency["relation_state"] != "active" or not dependency["accepted_basis_json"]:
            continue
        basis = _canonical_object(dependency["accepted_basis_json"], "topic dependency accepted_basis_json")
        identity = basis.get("authority", {}).get("published_identity")
        if isinstance(identity, str): retained.add(identity)
    return retained


def _canonical_object(value: Any, label: str) -> dict[str, Any]:
    if not isinstance(value, str):
        raise ProtocolError("state_corrupt", f"{label} must be canonical JSON")
    try:
        decoded = json.loads(value)
    except json.JSONDecodeError as error:
        raise ProtocolError("state_corrupt", f"{label} is invalid JSON") from error
    if not isinstance(decoded, dict) or _canonical_json(decoded) != value:
        raise ProtocolError("state_corrupt", f"{label} must be a canonical JSON object")
    return decoded


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
        raise ProtocolError("topic_gate_closed", "topic has an active closed requirements dependency", context={"topic_id": topic_id})


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
        basis = _canonical_object(dependency["accepted_basis_json"], "topic dependency accepted_basis_json") if dependency["accepted_basis_json"] else None
        authorities = basis.get("decision_authority") if basis else None
        known = isinstance(authorities, list) and bool(authorities) and all(isinstance(item, dict) and isinstance(item.get("decision_id"), str) for item in authorities)
        basis_ids = {item["decision_id"] for item in authorities} if known else set()
        authority = basis.get("authority", {}) if basis else {}
        authority_id = authority.get("result_id") or authority.get("checkpoint_id")
        if changed_decision_ids is None or not known or basis_ids & changed_decision_ids or (invalidated_authority_ids is not None and authority_id in invalidated_authority_ids):
            dependency["gate_state"] = "closed"
            dependency["record_revision"] += 1
            dependency["gate_reason_json"] = _canonical_json({"kind": "direct-upstream-invalidation", "ledger_revision": ledger_revision, **cause})
            closed.append(dependency["dependency_id"])
    return sorted(closed)


def _candidate_decisions(records: dict[str, list[dict[str, Any]]], prerequisite: str) -> list[dict[str, Any]]:
    result = []
    for decision in _topic_snapshot(records, prerequisite)["decisions"]:
        if decision.get("state") == "confirmed":
            digest = _sha256(_canonical_json(decision).encode("utf-8"))
            result.append({"decision_id": decision["decision_id"], "sha256": digest, "summary": decision.get("summary", "")})
    return sorted(result, key=lambda item: item["decision_id"])


def _candidates(records: dict[str, list[dict[str, Any]]], dependency: dict[str, Any]) -> list[dict[str, Any]]:
    prerequisite = dependency["prerequisite_topic_id"]
    if dependency["requirement_kind"] == "confirmed-decision":
        decisions = _candidate_decisions(records, prerequisite)
        return [{"authority_id": None, "authority": {"decision_set_digest": _sha256(_canonical_json(decisions).encode("utf-8"))}, "decision_authority": decisions}] if decisions else []
    if dependency["requirement_kind"] == "phase-0-checkpoint":
        result = []
        for record in records["Checkpoints"]:
            if record.get("topic_id") != prerequisite or record.get("state") != "completed":
                continue
            checkpoint = _json_field(record, "data_json", "checkpoint")
            if checkpoint.get("purpose") != "stage-entry" or checkpoint.get("stage_entry_phase") != 0 or not checkpoint.get("published_identity"):
                continue
            digests = json.loads(checkpoint.get("decision_digests_json", "{}"))
            if not isinstance(digests, dict):
                continue
            result.append({"authority_id": checkpoint["checkpoint_id"], "authority": {"checkpoint_id": checkpoint["checkpoint_id"], "record_revision": checkpoint["record_revision"], "published_identity": checkpoint["published_identity"], "decision_digest": checkpoint["decision_digest"]}, "decision_authority": [{"decision_id": key, "sha256": value} for key, value in sorted(digests.items())]})
        # A later completed Phase-0 entry checkpoint supersedes earlier draft
        # identities for release purposes.
        return result[-1:]
    result = []
    for record in records["Phase Results"]:
        if record.get("result_kind") != "phase-result" or record.get("state") != "completed":
            continue
        phase_result = _json_field(record, "data_json", "phase result")
        if phase_result.get("topic_id") != prerequisite or phase_result.get("from_phase") != 0 or phase_result.get("to_phase") != 1:
            continue
        ids = phase_result.get("affected_decision_ids")
        if not isinstance(ids, list): continue
        snapshot = {item["decision_id"]: item for item in _topic_snapshot(records, prerequisite)["decisions"]}
        if any(item not in snapshot or snapshot[item].get("state") != "confirmed" for item in ids):
            continue
        decisions = {item: _sha256(_canonical_json(snapshot[item]).encode("utf-8")) for item in ids}
        result.append({"authority_id": phase_result["result_id"], "authority": {"result_id": phase_result["result_id"], "record_revision": record["record_revision"], "state": "completed", "phase_run_id": phase_result["phase_run_id"], "affected_decision_ids": sorted(ids)}, "decision_authority": [{"decision_id": item, "sha256": decisions.get(item, "") } for item in sorted(ids)]})
    return sorted(result, key=lambda item: str(item["authority_id"]))


def has_current_authority(
    records: dict[str, list[dict[str, Any]]], topic_id: str,
) -> bool:
    """Whether a child result must name one protocol-current authority."""
    return any(_candidates(records, {
        "prerequisite_topic_id": topic_id,
        "requirement_kind": kind,
    }) for kind in KINDS)


def _normalize_authority_selection(
    candidates: list[dict[str, Any]], requirement_kind: str, authority_id: Any,
    decision_ids: Any,
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    """Select one current candidate and its exact frozen decision subset."""
    if not isinstance(decision_ids, list) or sorted(set(decision_ids)) != decision_ids:
        raise ProtocolError("invalid_request", "basis selection decision_ids must be sorted")
    if requirement_kind == "confirmed-decision" and not decision_ids:
        raise ProtocolError("topic_dependency_evidence_unavailable", "confirmed-decision requires one or more current decisions")
    matching = [item for item in candidates if item["authority_id"] == authority_id]
    if len(matching) != 1:
        raise ProtocolError("topic_dependency_evidence_unavailable", "selected dependency evidence is not current")
    candidate = matching[0]
    authority = {item["decision_id"]: item for item in candidate["decision_authority"]}
    if any(item not in authority or not authority[item].get("sha256") for item in decision_ids):
        raise ProtocolError("topic_dependency_evidence_unavailable", "selected dependency evidence is not current")
    return candidate, [authority[item] for item in decision_ids]


def freeze_authority_selection(
    records: dict[str, list[dict[str, Any]]], topic_id: str, selection: Any,
) -> dict[str, Any]:
    """Validate and freeze one exact current authority for a child result.

    This uses the same candidate model as gate evaluation so a child cannot
    smuggle arbitrary authority or decision IDs into a later parent release.
    """
    if not isinstance(selection, dict) or set(selection) != {
        "authority_kind", "authority_identity", "decision_ids",
    }:
        raise ProtocolError("invalid_request", "authority_selection is invalid")
    kind = selection["authority_kind"]
    identity = selection["authority_identity"]
    decision_ids = selection["decision_ids"]
    if kind not in KINDS or not isinstance(decision_ids, list) or sorted(set(decision_ids)) != decision_ids:
        raise ProtocolError("invalid_request", "authority_selection is invalid")
    if kind == "confirmed-decision":
        if identity is not None:
            raise ProtocolError("invalid_request", "confirmed-decision has no authority identity")
    elif not isinstance(identity, str):
        raise ProtocolError("invalid_request", "authority identity is required")
    candidates = _candidates(records, {
        "prerequisite_topic_id": topic_id,
        "requirement_kind": kind,
    })
    try:
        candidate, chosen = _normalize_authority_selection(
            candidates, kind, identity, decision_ids
        )
    except ProtocolError as error:
        raise ProtocolError(error.code, error.message.replace("dependency", "child-result")) from error
    frozen = {
        "authority_kind": kind,
        "authority_identity": identity,
        "decision_ids": decision_ids,
        "decision_authority": chosen,
        "topic_phase": _record_by_id(
            records["Current Topics"], "topic_id", topic_id, "topic_id"
        )["current_phase"],
    }
    if candidate.get("authority") is not None:
        frozen["authority"] = candidate["authority"]
    return frozen


def release_child_result_dependencies(
    records: dict[str, list[dict[str, Any]],], *, dependent_topic_id: str,
    prerequisite_topic_id: str, child_result_id: str, frozen_authority: Any,
    releases: Any, ledger_revision: int,
) -> list[str]:
    """Validate frozen child authority and atomically normalize matching gates."""
    if not isinstance(releases, list) or len(releases) > 64:
        raise ProtocolError("invalid_request", "dependency_releases must be a bounded list")
    if len({item.get("dependency_id") for item in releases if isinstance(item, dict)}) != len(releases):
        raise ProtocolError("invalid_request", "dependency_releases must name each dependency once")
    if not isinstance(frozen_authority, dict):
        raise ProtocolError("state_corrupt", "child result authority is invalid")
    if frozen_authority.get("authority_kind") == "none":
        if releases:
            raise ProtocolError("topic_dependency_state_conflict", "a child result without frozen authority cannot release a gate")
        return []
    normalized = freeze_authority_selection(records, prerequisite_topic_id, {
        "authority_kind": frozen_authority.get("authority_kind"),
        "authority_identity": frozen_authority.get("authority_identity"),
        "decision_ids": frozen_authority.get("decision_ids"),
    })
    if _canonical_json(normalized) != _canonical_json(frozen_authority):
        raise ProtocolError("topic_dependency_evidence_unavailable", "frozen child authority is no longer current")
    selected: list[tuple[dict[str, Any], list[str]]] = []
    pairs = {item["decision_id"]: item for item in normalized["decision_authority"]}
    for release in releases:
        if not isinstance(release, dict) or set(release) != {"dependency_id", "decision_ids", "authority_kind", "authority_identity"}:
            raise ProtocolError("invalid_request", "dependency release is invalid")
        ids = release["decision_ids"]
        dependency = _record_by_id(records["Topic Dependencies"], "dependency_id", release["dependency_id"], "dependency_id")
        if (
            release["authority_kind"] != normalized["authority_kind"]
            or release["authority_identity"] != normalized["authority_identity"]
            or dependency["requirement_kind"] != normalized["authority_kind"]
            or dependency["dependent_topic_id"] != dependent_topic_id
            or dependency["prerequisite_topic_id"] != prerequisite_topic_id
            or dependency["relation_state"] != "active" or dependency["gate_state"] != "closed"
            or not isinstance(ids, list) or sorted(set(ids)) != ids
            or any(item not in pairs for item in ids)
            or (dependency["requirement_kind"] == "confirmed-decision" and not ids)
        ):
            raise ProtocolError("topic_dependency_state_conflict", "child result does not match a current closed dependency")
        selected.append((dependency, ids))
    for dependency, ids in selected:
        basis = {"basis_version": 1, "dependency_id": dependency["dependency_id"], "prerequisite_topic_id": prerequisite_topic_id, "requirement_kind": normalized["authority_kind"], "child_result_id": child_result_id, "decision_authority": [pairs[item] for item in ids]}
        if "authority" in normalized:
            basis["authority"] = normalized["authority"]
        dependency["gate_state"] = "open"
        dependency["record_revision"] += 1
        dependency["accepted_basis_json"] = _canonical_json(basis)
        dependency["gate_reason_json"] = _canonical_json({"kind": "child-result-absorb-release", "ledger_revision": ledger_revision, "child_result_id": child_result_id})
    return sorted(item[0]["dependency_id"] for item in selected)


def _closed(records: dict[str, list[dict[str, Any]]], topic_id: str) -> list[dict[str, Any]]:
    return sorted((item for item in records["Topic Dependencies"] if item["dependent_topic_id"] == topic_id and item["relation_state"] == "active" and item["gate_state"] == "closed"), key=lambda item: item["dependency_id"])


def _evaluation(records: dict[str, list[dict[str, Any]]], topic_id: str, selection: list[dict[str, Any]] | None) -> dict[str, Any]:
    closed = _closed(records, topic_id)
    if not closed:
        return {"state": "open", "derived_gate_state": "open", "dependencies": []}
    choices = {item.get("dependency_id"): item for item in selection or []}
    if selection is not None and len(choices) != len(selection):
        raise ProtocolError("invalid_request", "basis_selection must name every closed dependency exactly once")
    details = []
    proposed = []
    for dependency in closed:
        prerequisite_topic = _record_by_id(records["Current Topics"], "topic_id", dependency["prerequisite_topic_id"], "prerequisite_topic_id")
        candidates = _candidates(records, dependency)
        selected = choices.get(dependency["dependency_id"])
        detail: dict[str, Any] = {"dependency_id": dependency["dependency_id"], "record_revision": dependency["record_revision"], "requirement_kind": dependency["requirement_kind"], "requirement_summary": dependency["requirement_summary"], "prerequisite_topic_id": dependency["prerequisite_topic_id"], "prerequisite_phase": prerequisite_topic["current_phase"], "prerequisite_state": prerequisite_topic["topic_state"], "candidates": candidates}
        if selected is not None:
            authority_id = selected.get("authority_id")
            if set(selected) != ({"dependency_id", "decision_ids"} if dependency["requirement_kind"] == "confirmed-decision" else {"dependency_id", "authority_id", "decision_ids"}):
                raise ProtocolError("topic_dependency_evidence_unavailable", "selected dependency evidence is not current")
            candidate, chosen = _normalize_authority_selection(
                candidates, dependency["requirement_kind"], authority_id,
                selected.get("decision_ids"),
            )
            basis = {"basis_version": 1, "dependency_id": dependency["dependency_id"], "prerequisite_topic_id": dependency["prerequisite_topic_id"], "requirement_kind": dependency["requirement_kind"], "decision_authority": [{"decision_id": item["decision_id"], "sha256": item["sha256"]} for item in chosen]}
            if candidate.get("authority") is not None:
                basis["authority"] = candidate["authority"]
            detail["proposed_basis"] = basis
            proposed.append({"dependency_id": dependency["dependency_id"], "record_revision": dependency["record_revision"], "basis": basis})
        elif not candidates:
            detail["waiting_reason"] = "current required authority is unavailable"
        details.append(detail)
    if selection is not None and len(choices) != len(closed):
        raise ProtocolError("invalid_request", "basis_selection must name every active closed dependency")
    releasable = all(item["candidates"] for item in details)
    result: dict[str, Any] = {"state": "releasable" if releasable else "blocked", "derived_gate_state": "closed", "dependencies": details}
    if releasable and selection is not None:
        release_set = sorted(proposed, key=lambda item: item["dependency_id"])
        result["release_set"] = release_set
        result["release_set_sha256"] = _sha256(_canonical_json({"ledger_revision": 0, "topic_id": topic_id, "release_set": release_set}).encode("utf-8"))
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


def _validate_new_edge(records: dict[str, list[dict[str, Any]]], dependent: str, prerequisite: str, kind: Any, summary: Any, *, replacing: str | None = None) -> None:
    if dependent == prerequisite:
        raise ProtocolError("topic_dependency_state_conflict", "a topic cannot depend on itself")
    _record_by_id(records["Current Topics"], "topic_id", prerequisite, "prerequisite_topic_id")
    topic = _record_by_id(records["Current Topics"], "topic_id", dependent, "dependent_topic_id")
    if topic.get("current_phase") not in {0, 1}:
        raise ProtocolError("topic_dependency_phase_conflict", "topic dependencies are mutable only in Phase 0 or 1")
    if kind not in KINDS or not isinstance(summary, str) or not summary or len(summary.encode("utf-8")) > 4096:
        raise ProtocolError("invalid_request", "topic dependency requirement is invalid")
    copied = [dict(item) for item in records["Topic Dependencies"] if item["dependency_id"] != replacing]
    copied.append(_new_dependency_record(dependency_id="DEP-probe", dependent_topic_id=dependent, prerequisite_topic_id=prerequisite, requirement_kind=kind, requirement_summary=summary, reason={"kind": "probe"}))
    try:
        _validate_dependency_records({**records, "Topic Dependencies": copied})
    except ProtocolError as error:
        if "cycle" in error.message:
            raise ProtocolError("topic_dependency_cycle", "active dependency graph would contain a cycle") from error
        if "duplicated" in error.message:
            raise ProtocolError("topic_dependency_duplicate", "active dependency endpoint pair is duplicated") from error
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
    if action not in {"create", "replace", "cancel"}:
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
        if topic["current_phase"] not in {0, 1}: raise ProtocolError("topic_dependency_phase_conflict", "topic dependencies are immutable after Phase 1")
        if action == "create":
            _validate_new_edge(records, request["actor_topic_id"], request["prerequisite_topic_id"], request["requirement_kind"], request["requirement_summary"])
            dep = _new_dependency_record(dependency_id=_dependency_id(request["idempotency_key"]), dependent_topic_id=request["actor_topic_id"], prerequisite_topic_id=request["prerequisite_topic_id"], requirement_kind=request["requirement_kind"], requirement_summary=request["requirement_summary"], reason={"kind": "explicit-create", "ledger_revision": revision + 1})
            records["Topic Dependencies"].append(dep)
        else:
            dep = _record_by_id(records["Topic Dependencies"], "dependency_id", request["dependency_id"], "dependency_id")
            if dep["dependent_topic_id"] != request["actor_topic_id"]: raise ProtocolError("topic_dependency_ownership_conflict", "only the dependent topic may mutate a dependency")
            if request["expected_dependency_revision"] != dep["record_revision"]: raise ProtocolError("record_revision_conflict", "dependency revision is stale", context={"record_revision": dep["record_revision"]})
            if action == "replace":
                _validate_new_edge(records, request["actor_topic_id"], request["prerequisite_topic_id"], request["requirement_kind"], request["requirement_summary"], replacing=dep["dependency_id"])
                dep.update({"prerequisite_topic_id": request["prerequisite_topic_id"], "requirement_kind": request["requirement_kind"], "requirement_summary": request["requirement_summary"], "relation_state": "active", "gate_state": "closed"})
            else:
                dep["relation_state"] = "cancelled"
            dep["record_revision"] += 1
            dep["gate_reason_json"] = _canonical_json({"kind": f"explicit-{action}", "ledger_revision": revision + 1})
        result = {"ok": True, "state": action, "idempotent_replay": False, "dependency_id": dep["dependency_id"], "dependency_revision": dep["record_revision"], "ledger_revision": revision + 1, "record_revision": topic_revision, "derived_gate_state": derived_gate(records, request["actor_topic_id"])}
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
        if selection is not None and (not isinstance(selection, list) or len(selection) > 64): raise ProtocolError("invalid_request", "basis_selection is invalid")
        result = _evaluation(records, request["actor_topic_id"], selection)
        result.update({"ok": True, "ledger_revision": int(frontmatter["ledger_revision"]), "record_revision": topic["record_revision"], "topic_id": request["actor_topic_id"]})
        if "release_set" in result:
            result["release_set_sha256"] = _sha256(_canonical_json({"ledger_revision": result["ledger_revision"], "topic_revision": topic["record_revision"], "release_set": result["release_set"]}).encode("utf-8"))
        return result


def release_topic_gate(request: dict[str, Any]) -> dict[str, Any]:
    _, ledger_path, _, lock_path, owner_ref = _request_context(request, {"release_set", "release_set_sha256"})
    with lock_path.open("a+b") as stream:
        _flock_with_timeout(stream)
        frontmatter, records = _load_records(ledger_path)
        replay = _idempotent_result(records, request)
        if replay is not None: return replay
        topic = _record_by_id(records["Current Topics"], "topic_id", request["actor_topic_id"], "topic_id")
        revision, topic_revision = _validate_revisions(request, frontmatter, topic)
        _verify_topic_owner(records, request["actor_topic_id"], owner_ref)
        if topic["current_phase"] not in {0, 1}:
            raise ProtocolError("topic_dependency_phase_conflict", "topic dependencies are immutable after Phase 1")
        selection = [{"dependency_id": item.get("dependency_id"), "decision_ids": [entry["decision_id"] for entry in item.get("basis", {}).get("decision_authority", [])], **({"authority_id": item["basis"]["authority"].get("checkpoint_id", item["basis"]["authority"].get("result_id"))} if isinstance(item.get("basis"), dict) and "authority" in item["basis"] else {})} for item in request["release_set"]] if isinstance(request["release_set"], list) else None
        evaluation = _evaluation(records, request["actor_topic_id"], selection)
        expected = _sha256(_canonical_json({"ledger_revision": revision, "topic_revision": topic_revision, "release_set": evaluation.get("release_set")}).encode("utf-8"))
        if evaluation.get("state") != "releasable" or request["release_set"] != evaluation.get("release_set") or request["release_set_sha256"] != expected:
            raise ProtocolError("topic_gate_evaluation_stale", "topic gate evaluation is stale")
        for item in _closed(records, request["actor_topic_id"]):
            match = next(entry for entry in evaluation["release_set"] if entry["dependency_id"] == item["dependency_id"])
            item["gate_state"] = "open"; item["record_revision"] += 1; item["accepted_basis_json"] = _canonical_json(match["basis"]); item["gate_reason_json"] = _canonical_json({"kind": "atomic-release", "ledger_revision": revision + 1})
        result = {"ok": True, "state": "open", "idempotent_replay": False, "ledger_revision": revision + 1, "record_revision": topic_revision, "derived_gate_state": "open", "accepted_bases": evaluation["release_set"]}
        _write_ledger_transaction(ledger_path, frontmatter, records, request, ledger_revision=revision + 1, event_type="topic-gate-released", result=result)
        return result
