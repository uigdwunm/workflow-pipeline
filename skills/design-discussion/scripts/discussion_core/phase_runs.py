"""Lifecycle Phase Run preparation, coordination, completion, and recovery."""

from __future__ import annotations

import json
from pathlib import Path
import uuid
from typing import Any

from .checkpoints import _checkpoint_data, _completed_checkpoint
from .state import (
    ProtocolError,
    SHA256_RE,
    _canonical_json,
    _evolution_paths,
    _expect_keys,
    _expect_string,
    _flock_with_timeout,
    _idempotent_result,
    _json_field,
    _load_records,
    _record_by_id,
    _require_regular_nosymlink,
    _sha256,
    _topic_snapshot,
    _validate_revisions,
    _validate_uuid4,
    _validated_string_list,
    _verify_topic_path_authority,
    _verify_topic_owner,
    _write_ledger_transaction,
)
from .topic_dependencies import reclose_directly_affected, require_open_gate


PHASE_ROUTES = {(0, 1), (0, 2), (1, 2), (2, 3), (3, 4)}
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
    topic_record = _record_by_id(
        records["Current Topics"],
        "topic_id",
        data["source_topic_id"],
        "topic_id",
    )
    topic_path = _verify_topic_path_authority(
        topic_record,
        topic_path,
        records=records,
    )
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
            and authority.get("confirmation_intent") == "continuous"
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
            "confirmation_intent",
            "user_reply",
        },
    )[:4]
    user_reply = _expect_string(request["user_reply"], "user_reply", max_bytes=128)
    confirmation_intent = _expect_string(
        request["confirmation_intent"], "confirmation_intent", max_bytes=32
    )
    source_checkpoint_identity = _expect_string(
        request["source_checkpoint_identity"],
        "source_checkpoint_identity",
        max_bytes=128,
    )
    requested_phase_result_id = _expect_string(
        request["phase_result_id"], "phase_result_id", max_bytes=64
    )
    if confirmation_intent != "continuous":
        raise ProtocolError(
            "phase_flow_mode_invalid",
            "continuous authorization requires normalized continuous intent",
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
        require_open_gate(records, request["actor_topic_id"])
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
            "confirmation_intent": confirmation_intent,
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
            "scope",
        },
    )[:4]
    from_phase = request["from_phase"]
    to_phase = request["to_phase"]
    if not isinstance(from_phase, int) or not isinstance(to_phase, int):
        raise ProtocolError("invalid_request", "phase values must be integers")
    route = (from_phase, to_phase)
    if route not in PHASE_ROUTES:
        raise ProtocolError("invalid_phase_route", "only the five approved forward routes are legal")
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
        require_open_gate(records, request["actor_topic_id"])
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
        if data.get("from_phase") in {0, 1}:
            require_open_gate(records, request["actor_topic_id"])
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
    topic_path = _verify_topic_path_authority(
        topic,
        topic_path,
        records=records,
    )
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
        raise ProtocolError("invalid_phase_route", "only the five approved forward routes are legal")
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
        if route[0] in {0, 1}:
            require_open_gate(records, request["actor_topic_id"])
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
        require_open_gate(records, request["actor_topic_id"])
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
        if target in {"ready", "active"} and data.get("from_phase") in {0, 1}:
            require_open_gate(records, request["actor_topic_id"])
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
        changed = {decision_id for decision_id, action in review.items() if action != "keep"}
        if changed:
            reclose_directly_affected(
                records, prerequisite_topic_id=request["actor_topic_id"],
                changed_decision_ids=changed,
                cause={"kind": "phase-reopen", "affected_decision_ids": sorted(changed)},
                ledger_revision=ledger_revision + 1,
            )
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
