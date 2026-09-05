"""Authority discovery, freezing, and child-result provenance for dependencies."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .checkpoint_authority import CheckpointAuthorityCorrupt, checkpoint_decision_fields, current_checkpoint_artifact
from .state import (
    ProtocolError,
    _canonical_json,
    _json_field,
    _record_by_id,
    _require_regular_nosymlink,
    _sha256,
    _topic_snapshot,
)
from .topic_dependency_schema import (
    AuthoritySelection,
    DEPENDENCY_AUTHORITY_KINDS,
    authority_descriptor,
    canonical_object,
    canonical_string_array,
    decision_authority,
    decision_pair_digest,
    validate_dependency_records,
)

def _state_corrupt(code: str, message: str) -> None:
    raise ProtocolError(code, message)

def _invalid_request(code: str, message: str) -> None:
    raise ProtocolError(code, message)


def retained_checkpoint_identities(records: dict[str, list[dict[str, Any]]]) -> set[str]:
    """Return snapshots referenced by active bases and live frozen child authority.

    This stays with authority provenance instead of making checkpoint GC know
    how dependency and child-result records encode their frozen selections.
    """
    retained: set[str] = set()
    for dependency in records["Topic Dependencies"]:
        if dependency["relation_state"] != "active" or not dependency["accepted_basis_json"]:
            continue
        basis = canonical_object(
            dependency["accepted_basis_json"],
            "topic dependency accepted_basis_json",
            _state_corrupt,
        )
        identity = basis.get("authority", {}).get("published_identity")
        if isinstance(identity, str):
            retained.add(identity)
    for result in records["Phase Results"]:
        if (
            result.get("result_kind") != "child-topic-result"
            or result.get("state") not in {"pending", "impact-recorded"}
        ):
            continue
        authority = canonical_object(
            result.get("authority_json"), "child result authority_json", _state_corrupt
        )
        if authority.get("authority_kind") != "phase-0-checkpoint":
            continue
        identity = authority.get("authority", {}).get("published_identity")
        if isinstance(identity, str):
            retained.add(identity)
    return retained


def _candidate_decisions(records: dict[str, list[dict[str, Any]]], prerequisite: str) -> list[dict[str, Any]]:
    decisions = _topic_snapshot(records, prerequisite)["decisions"]
    descriptor, _, _ = decision_authority(decisions)
    summaries = {item["decision_id"]: item.get("summary", "") for item in decisions}
    return [
        {**item, "summary": summaries[item["decision_id"]]}
        for item in descriptor
    ]


def _confirmed_decision_candidates(
    records: dict[str, list[dict[str, Any]]], prerequisite: str,
) -> list[dict[str, Any]]:
    decisions = _candidate_decisions(records, prerequisite)
    return [{
        "authority_id": None,
        "authority": {"decision_set_digest": decision_pair_digest(decisions)},
        "decision_authority": decisions,
    }] if decisions else []


def decision_authority_for_topic(
    records: dict[str, list[dict[str, Any]]], topic_id: str,
) -> tuple[list[dict[str, Any]], dict[str, str], str]:
    """Return the one canonical decision descriptor used by every authority kind."""
    return decision_authority(_topic_snapshot(records, topic_id)["decisions"])


def published_stage_entry_authority(
    records: dict[str, list[dict[str, Any]]], topic_id: str, phase: int,
) -> dict[str, Any] | None:
    """Project the one still-current stage-entry authority for a topic phase.

    A Phase-0 checkpoint is consumed by a later Phase Result.  It is not
    resurrected when that result is reopened, so reopened Phase 0 can publish
    fresh authority after its dependency set changes.
    """
    if phase == 0:
        for result in records["Phase Results"]:
            if (
                result.get("result_kind") != "phase-result"
                or result.get("state") not in {"completed", "review-pending"}
            ):
                continue
            try:
                data = json.loads(str(result.get("data_json")))
            except (TypeError, ValueError, json.JSONDecodeError) as error:
                raise ProtocolError("state_corrupt", "Phase Result authority is corrupt") from error
            if data.get("topic_id") == topic_id and data.get("from_phase") == 0:
                return None
    for record in reversed(records["Checkpoints"]):
        if record.get("topic_id") != topic_id or record.get("state") != "completed":
            continue
        try:
            checkpoint = json.loads(str(record.get("data_json")))
        except (TypeError, ValueError, json.JSONDecodeError) as error:
            raise ProtocolError("state_corrupt", "published checkpoint is corrupt") from error
        if (
            checkpoint.get("purpose") != "stage-entry"
            or checkpoint.get("stage_entry_phase") != phase
            or not checkpoint.get("published_identity")
        ):
            continue
        if phase == 0:
            return {
                "authority_kind": "phase-0-checkpoint",
                "authority_id": checkpoint.get("checkpoint_id"),
            }
        if phase == 1 and checkpoint.get("stage_entry_phase_result_id"):
            return {
                "authority_kind": "phase-1-result",
                "authority_id": checkpoint.get("stage_entry_phase_result_id"),
            }
    return None


def _current_checkpoint(record: dict[str, Any], checkpoint: dict[str, Any], records: dict[str, list[dict[str, Any]]], prerequisite: str) -> bool:
    """Verify a completed Phase-0 artifact before exposing it as authority."""
    try:
        if (
            record.get("checkpoint_id") != checkpoint.get("checkpoint_id")
            or record.get("state") != "completed"
            or record.get("record_revision") != checkpoint.get("record_revision")
            or checkpoint.get("topic_id") != prerequisite
            or checkpoint.get("purpose") != "stage-entry"
            or checkpoint.get("stage_entry_phase") != 0
            or checkpoint.get("state") != "completed"
            or not isinstance(checkpoint.get("published_identity"), str)
        ):
            return False
        topic = _record_by_id(records["Current Topics"], "topic_id", prerequisite, "topic_id")
        topic_path = topic.get("topic_document_path")
        def artifact_bytes(path: Path, label: str) -> bytes | None:
            try:
                return _require_regular_nosymlink(path, label)
            except ProtocolError:
                return None
        if (
            not isinstance(topic_path, str)
            or current_checkpoint_artifact(
                checkpoint, topic_path=Path(topic_path), sha256=_sha256,
                canonical_json=_canonical_json, read_regular=artifact_bytes,
            ) is None
        ):
            return False
        _, current_digests, current_digest = decision_authority_for_topic(records, prerequisite)
        frozen_digests, frozen_confirmed = checkpoint_decision_fields(checkpoint)
        return (
            current_digests == frozen_digests
            and current_digest == checkpoint.get("decision_digest")
            and sorted(
                item["decision_id"]
                for item in _topic_snapshot(records, prerequisite)["decisions"]
                if item.get("state") == "confirmed"
            ) == frozen_confirmed
        )
    except CheckpointAuthorityCorrupt as error:
        raise ProtocolError("state_corrupt", "checkpoint authority is corrupt") from error
    except ProtocolError as error:
        raise ProtocolError("state_corrupt", "checkpoint authority is corrupt") from error
    except (KeyError, TypeError, ValueError, UnicodeDecodeError, json.JSONDecodeError) as error:
        raise ProtocolError("state_corrupt", "checkpoint authority is corrupt") from error


def _checkpoint_candidates(
    records: dict[str, list[dict[str, Any]]], prerequisite: str,
) -> list[dict[str, Any]]:
    authorities = []
    for record in records["Checkpoints"]:
        if record.get("topic_id") != prerequisite:
            continue
        checkpoint = _json_field(record, "data_json", "checkpoint")
        if (
            checkpoint.get("purpose") == "stage-entry"
            and checkpoint.get("stage_entry_phase") == 0
        ):
            authorities.append(record)
    if not authorities:
        return []
    # The latest persisted authority is decisive: an invalid replacement must
    # fail closed rather than silently restoring an older checkpoint.
    record = authorities[-1]
    try:
        checkpoint = _json_field(record, "data_json", "checkpoint")
        if record.get("state") != "completed":
            return []
        if not _current_checkpoint(record, checkpoint, records, prerequisite):
            return []
        digests, confirmed_ids = checkpoint_decision_fields(checkpoint)
    except ProtocolError as error:
        raise ProtocolError("state_corrupt", "latest checkpoint authority is corrupt") from error
    except (KeyError, TypeError, ValueError, json.JSONDecodeError) as error:
        raise ProtocolError("state_corrupt", "latest checkpoint authority is corrupt") from error
    return [{"authority_id": checkpoint["checkpoint_id"], "authority": {"checkpoint_id": checkpoint["checkpoint_id"], "record_revision": checkpoint["record_revision"], "published_identity": checkpoint["published_identity"], "decision_digest": checkpoint["decision_digest"]}, "decision_authority": [{"decision_id": key, "sha256": digests[key]} for key in confirmed_ids]}]


def _phase_result_candidates(
    records: dict[str, list[dict[str, Any]]], prerequisite: str,
) -> list[dict[str, Any]]:
    result = []
    for record in records["Phase Results"]:
        if record.get("result_kind") != "phase-result" or record.get("state") != "completed":
            continue
        try:
            phase_result = _json_field(record, "data_json", "phase result")
            ids = phase_result["affected_decision_ids"]
            frozen = phase_result["decision_authority"]
            if (
                record.get("result_id") != phase_result.get("result_id")
            ):
                raise ProtocolError("state_corrupt", "completed Phase Result envelope is incoherent")
            if (
                phase_result.get("topic_id") != prerequisite
                or phase_result.get("from_phase") != 0
                or phase_result.get("to_phase") != 1
            ):
                continue
            if (
                not isinstance(phase_result.get("phase_run_id"), str)
                or not isinstance(ids, list)
                or ids != sorted(set(ids))
                or not isinstance(frozen, list)
                or frozen != sorted(frozen, key=lambda item: item.get("decision_id", ""))
                or any(not isinstance(item, dict) or set(item) != {"decision_id", "sha256"} for item in frozen)
                or [item["decision_id"] for item in frozen] != ids
            ):
                raise ProtocolError("state_corrupt", "completed Phase Result envelope is incoherent")
            current, _, _ = decision_authority_for_topic(records, prerequisite)
            current_by_id = {
                item["decision_id"]: {
                    "decision_id": item["decision_id"], "sha256": item["sha256"]
                }
                for item in current
            }
            if any(current_by_id.get(item["decision_id"]) != item for item in frozen):
                continue
            phase_run = _record_by_id(records["Phase Runs"], "run_id", phase_result["phase_run_id"], "phase_run_id")
            phase_data = _json_field(phase_run, "data_json", "phase run")
            if (
                phase_run.get("run_kind") != "phase-run"
                or phase_run.get("state") != "completed"
                or phase_data.get("source_topic_id") != prerequisite
                or phase_data.get("from_phase") != 0
                or phase_data.get("to_phase") != 1
            ):
                raise ProtocolError("state_corrupt", "completed Phase Result provenance is incoherent")
        except ProtocolError as error:
            raise ProtocolError("state_corrupt", "Phase Result authority is corrupt") from error
        except (KeyError, TypeError) as error:
            raise ProtocolError("state_corrupt", "Phase Result authority is corrupt") from error
        result.append({"authority_id": phase_result["result_id"], "authority": {"result_id": phase_result["result_id"], "record_revision": record["record_revision"], "state": "completed", "phase_run_id": phase_result["phase_run_id"], "affected_decision_ids": ids}, "decision_authority": frozen})
    return sorted(result, key=lambda item: str(item["authority_id"]))


CANDIDATE_BUILDERS = {
    "confirmed": _confirmed_decision_candidates,
    "checkpoint": _checkpoint_candidates,
    "phase_result": _phase_result_candidates,
}


def authority_candidates(
    records: dict[str, list[dict[str, Any]]], dependency: dict[str, Any],
) -> list[dict[str, Any]]:
    kind = dependency["requirement_kind"]
    descriptor = authority_descriptor(kind)
    return CANDIDATE_BUILDERS[descriptor.candidate_key](
        records, dependency["prerequisite_topic_id"]
    )


def has_current_authority(
    records: dict[str, list[dict[str, Any]]], topic_id: str,
) -> bool:
    """Whether a child result must name one protocol-current authority."""
    return any(authority_candidates(records, {
        "prerequisite_topic_id": topic_id,
        "requirement_kind": kind,
    }) for kind in DEPENDENCY_AUTHORITY_KINDS)


def current_topic_authorities(
    records: dict[str, list[dict[str, Any]]], topic_id: str,
) -> list[dict[str, Any]]:
    """Expose protocol-derived current authority for child-result submission."""
    result: list[dict[str, Any]] = []
    for kind in sorted(DEPENDENCY_AUTHORITY_KINDS):
        for candidate in authority_candidates(records, {
            "prerequisite_topic_id": topic_id, "requirement_kind": kind,
        }):
            result.append({
                "authority_kind": kind,
                "authority_identity": candidate["authority_id"],
                "decision_authority": candidate["decision_authority"],
                "authority": candidate.get("authority"),
            })
    return result


def normalize_authority_selection(
    candidates: list[dict[str, Any]], requirement_kind: str, authority_id: Any,
    decision_ids: Any,
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    """Select one current candidate and its exact frozen decision subset."""
    if not canonical_string_array(decision_ids):
        raise ProtocolError("invalid_request", "basis selection decision_ids must be sorted")
    if authority_descriptor(requirement_kind).requires_decisions and not decision_ids:
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
    parsed = AuthoritySelection.parse(selection, _invalid_request)
    kind = parsed.authority_kind
    identity = parsed.authority_identity
    decision_ids = list(parsed.decision_ids)
    candidates = authority_candidates(records, {
        "prerequisite_topic_id": topic_id,
        "requirement_kind": kind,
    })
    try:
        candidate, chosen = normalize_authority_selection(
            candidates, kind, identity, decision_ids
        )
    except ProtocolError as error:
        raise ProtocolError(error.code, error.message.replace("dependency", "child-result")) from error
    frozen = {
        **parsed.as_request_fields(),
        "decision_authority": [
            {"decision_id": item["decision_id"], "sha256": item["sha256"]}
            for item in chosen
        ],
        "topic_phase": _record_by_id(
            records["Current Topics"], "topic_id", topic_id, "topic_id"
        )["current_phase"],
    }
    authority = parsed.descriptor.authority_for_selection(
        candidate, frozen["decision_authority"]
    )
    if authority is not None:
        frozen["authority"] = authority
    return frozen


def release_child_result_dependencies(
    records: dict[str, list[dict[str, Any]],], *, dependent_topic_id: str,
    prerequisite_topic_id: str, child_result_id: str, frozen_authority: Any,
    releases: Any, ledger_revision: int, absorb_operation_id: str,
) -> list[str]:
    """Validate frozen child authority and atomically normalize matching gates."""
    if not isinstance(releases, list) or len(releases) > 64:
        raise ProtocolError("invalid_request", "dependency_releases must be a bounded list")
    if not releases:
        return []
    if (
        any(not isinstance(item, dict) or not isinstance(item.get("dependency_id"), str) for item in releases)
        or [item["dependency_id"] for item in releases] != sorted(item["dependency_id"] for item in releases)
    ):
        raise ProtocolError("invalid_request", "dependency_releases must be canonically sorted")
    if len({item["dependency_id"] for item in releases}) != len(releases):
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
            or not canonical_string_array(ids)
            or any(item not in pairs for item in ids)
            or (authority_descriptor(dependency["requirement_kind"]).requires_decisions and not ids)
        ):
            raise ProtocolError("topic_dependency_state_conflict", "child result does not match a current closed dependency")
        selected.append((dependency, ids))
    for dependency, ids in selected:
        descriptor = authority_descriptor(normalized["authority_kind"])
        basis = descriptor.basis_from_candidate(
            {"authority": normalized.get("authority")},
            dependency_id=dependency["dependency_id"],
            prerequisite_topic_id=prerequisite_topic_id,
            decision_authority=[
                {"decision_id": pairs[item]["decision_id"], "sha256": pairs[item]["sha256"]}
                for item in ids
            ],
            child_result_id=child_result_id,
        )
        dependency["gate_state"] = "open"
        dependency["record_revision"] += 1
        dependency["accepted_basis_json"] = _canonical_json(basis)
        dependency["gate_reason_json"] = _canonical_json({"kind": "child-result-absorb-release", "absorb_operation_id": absorb_operation_id, "ledger_revision": ledger_revision, "child_result_id": child_result_id})
    validate_dependency_records(records, ProtocolError)
    return sorted(item[0]["dependency_id"] for item in selected)
