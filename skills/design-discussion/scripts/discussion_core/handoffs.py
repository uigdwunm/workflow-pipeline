"""Discussion handoff identity, binding, recovery, and child-result operations."""

from __future__ import annotations

import json
from pathlib import Path
import uuid
from typing import Any

from .state import (
    ProtocolError,
    ROOT_SLUG_RE,
    SHA256_RE,
    _canonical_json,
    _evolution_paths,
    _expect_keys,
    _expect_string,
    _flock_with_timeout,
    _idempotent_result,
    _inject_failure,
    _json_field,
    _load_records,
    _mutation_fingerprint,
    _record_by_id,
    _sha256,
    _topic_snapshot,
    _validate_revisions,
    _validate_uuid4,
    _validated_string_list,
    _verify_topic_owner,
    _write_ledger_transaction,
)
from .topic_dependencies import (
    freeze_authority_selection,
    prepare_initial_dependencies,
    require_open_gate,
)


HANDOFF_KINDS = {"child", "continuation"}
HANDOFF_WORK_SNAPSHOT_FIELDS = {
    "goal",
    "confirmed_decisions",
    "candidate_solution",
    "tentative_assumptions",
    "facts",
    "pending_questions",
}
HANDOFF_WORK_SNAPSHOT_MAX_BYTES = 16000
HANDOFF_PAYLOAD_MAX_BYTES = 32000


def _handoff_id(idempotency_key: str) -> str:
    return f"H-{uuid.UUID(idempotency_key).hex}"


def _handoff_attempt_id(handoff_id: str, number: int) -> str:
    return f"A-{handoff_id[2:]}-{number}"


def _handoff_record(
    records: dict[str, list[dict[str, Any]]], handoff_id: Any
) -> dict[str, Any]:
    return _record_by_id(
        records["Phase Runs"],
        "run_id",
        _expect_string(handoff_id, "handoff_id", max_bytes=64),
        "handoff_id",
    )


def _handoff_data(record: dict[str, Any]) -> dict[str, Any]:
    data = _json_field(record, "data_json", "handoff")
    if (
        not isinstance(data, dict)
        or record.get("run_kind") != "discussion-handoff"
        or record.get("run_id") != data.get("handoff_id")
        or record.get("state") != data.get("state")
        or record.get("record_revision") != data.get("record_revision")
    ):
        raise ProtocolError("state_corrupt", "handoff record envelope is invalid")
    return data


def _store_handoff(record: dict[str, Any], handoff: dict[str, Any]) -> None:
    record["state"] = handoff["state"]
    record["record_revision"] = handoff["record_revision"]
    record["data_json"] = _canonical_json(handoff)


def _handoff_attempt(handoff: dict[str, Any], attempt_id: Any) -> dict[str, Any]:
    value = _expect_string(attempt_id, "attempt_id", max_bytes=80)
    matches = [attempt for attempt in handoff["attempts"] if attempt.get("attempt_id") == value]
    if len(matches) != 1:
        raise ProtocolError("record_not_found", "attempt_id does not identify one handoff attempt")
    return matches[0]


def _validate_work_snapshot(value: Any) -> tuple[dict[str, Any], int]:
    if not isinstance(value, dict) or "goal" not in value or not set(value).issubset(
        HANDOFF_WORK_SNAPSHOT_FIELDS
    ):
        raise ProtocolError("invalid_request", "work_snapshot fields are not allowlisted")
    snapshot: dict[str, Any] = {}
    for key, item in value.items():
        if key == "goal":
            snapshot[key] = _expect_string(item, "work_snapshot.goal", max_bytes=24000)
        else:
            snapshot[key] = _validated_string_list(
                item, f"work_snapshot.{key}", allow_empty=True
            )
    size = len(_canonical_json(snapshot).encode("utf-8"))
    if size > HANDOFF_WORK_SNAPSHOT_MAX_BYTES:
        raise ProtocolError("handoff_payload_too_large", "work snapshot exceeds its size limit")
    return snapshot, size


def _validate_authoritative_references(value: Any) -> tuple[list[dict[str, str]], str]:
    if not isinstance(value, list) or len(value) > 64:
        raise ProtocolError("invalid_request", "authoritative_references must be a bounded array")
    result = []
    for index, item in enumerate(value):
        if not isinstance(item, dict):
            raise ProtocolError("invalid_request", f"authoritative_references[{index}] must be an object")
        _expect_keys(item, {"kind", "identity", "sha256"}, "authoritative reference")
        digest = _expect_string(item["sha256"], "authoritative reference sha256", max_bytes=64)
        if not SHA256_RE.fullmatch(digest):
            raise ProtocolError("invalid_request", "authoritative reference sha256 is invalid")
        result.append(
            {
                "kind": _expect_string(item["kind"], "authoritative reference kind", max_bytes=64),
                "identity": _expect_string(item["identity"], "authoritative reference identity", max_bytes=1024),
                "sha256": digest,
            }
        )
    return result, _sha256(_canonical_json(result).encode("utf-8"))


def _handoff_payload(
    handoff: dict[str, Any], attempt_id: str
) -> tuple[dict[str, Any], str, int]:
    envelope = {
        "project_id": handoff["project_id"],
        "tree_id": handoff["tree_id"],
        "topic_id": handoff["target_topic_id"],
        "parent_topic_id": handoff["source_topic_id"] if handoff["kind"] == "child" else None,
        "handoff_id": handoff["handoff_id"],
        "attempt_id": attempt_id,
    }
    payload = {
        "identity_envelope": envelope,
        "work_snapshot": handoff["work_snapshot"],
        "authoritative_references": handoff["authoritative_references"],
        "initial_dependencies": handoff.get("initial_dependencies", []),
    }
    payload_bytes = len(_canonical_json(payload).encode("utf-8"))
    if payload_bytes > HANDOFF_PAYLOAD_MAX_BYTES:
        raise ProtocolError("handoff_payload_too_large", "handoff payload exceeds its size limit")
    return envelope, _sha256(_canonical_json(payload).encode("utf-8")), payload_bytes


def _handoff_result(
    request: dict[str, Any],
    handoff: dict[str, Any],
    attempt: dict[str, Any],
    *,
    ledger_revision: int,
    topic_revision: int,
    state: str | None = None,
) -> dict[str, Any]:
    return {
        "ok": True,
        "state": state or handoff["state"],
        "idempotent_replay": False,
        "project_id": request["project_id"],
        "tree_id": request["tree_id"],
        "topic_id": request["actor_topic_id"],
        "target_topic_id": handoff["target_topic_id"],
        "ledger_revision": ledger_revision,
        "record_revision": topic_revision,
        "handoff_id": handoff["handoff_id"],
        "attempt_id": attempt["attempt_id"],
        "payload_sha256": attempt["payload_sha256"],
        "authoritative_references_sha256": handoff["authoritative_references_sha256"],
    }


def _prepare_handoff(request: dict[str, Any]) -> dict[str, Any]:
    project, ledger_path, _, lock_path, owner_ref = _evolution_paths(request)
    allowed = {
            "protocol_version", "operation", "project_path", "project_id", "tree_id",
            "actor_topic_id", "actor_conversation_ref", "expected_ledger_revision",
            "expected_topic_revision", "idempotency_key", "handoff_kind", "target_slug",
            "scope", "work_snapshot", "authoritative_references",
        }
    if "initial_dependencies" in request:
        allowed.add("initial_dependencies")
    _expect_keys(request, allowed, "prepare-handoff request")
    _validate_uuid4(request["idempotency_key"], "idempotency_key")
    kind = _expect_string(request["handoff_kind"], "handoff_kind", max_bytes=32)
    if kind not in HANDOFF_KINDS:
        raise ProtocolError("invalid_request", "handoff_kind is unsupported")
    if kind != "child" and "initial_dependencies" in request:
        raise ProtocolError("invalid_request", "initial_dependencies is valid only for a child handoff")
    target_slug = _expect_string(request["target_slug"], "target_slug", max_bytes=128)
    if not ROOT_SLUG_RE.fullmatch(target_slug):
        raise ProtocolError("invalid_root_slug", "target_slug is invalid")
    scope = _validated_string_list(request["scope"], "scope")
    work_snapshot, snapshot_bytes = _validate_work_snapshot(request["work_snapshot"])
    references, references_digest = _validate_authoritative_references(
        request["authoritative_references"]
    )
    with lock_path.open("a+b") as lock_stream:
        _flock_with_timeout(lock_stream)
        frontmatter, records = _load_records(ledger_path)
        handoff_id = _handoff_id(request["idempotency_key"])
        existing_records = [
            record for record in records["Phase Runs"] if record.get("run_id") == handoff_id
        ]
        if len(existing_records) > 1:
            raise ProtocolError("state_corrupt", "handoff creation identity is duplicated")
        if existing_records:
            existing = _handoff_data(existing_records[0])
            fingerprint = _mutation_fingerprint(request)
            if (
                existing.get("creation_idempotency_key") != request["idempotency_key"]
                or existing.get("creation_fingerprint") != fingerprint
            ):
                raise ProtocolError(
                    "idempotency_conflict",
                    "handoff creation identity was reused with different frozen intent",
                )
            creation_result = json.loads(existing["creation_result_json"])
            creation_result["idempotent_replay"] = True
            creation_result["state"] = existing["state"]
            return creation_result
        replay = _idempotent_result(records, request)
        if replay is not None:
            return replay
        source_topic = _record_by_id(
            records["Current Topics"], "topic_id", request["actor_topic_id"], "topic_id"
        )
        ledger_revision, topic_revision = _validate_revisions(request, frontmatter, source_topic)
        _verify_topic_owner(records, request["actor_topic_id"], owner_ref)
        require_open_gate(records, request["actor_topic_id"])
        target_topic_id = (
            request["actor_topic_id"] if kind == "continuation" else f"topic-{uuid.UUID(request['idempotency_key']).hex}"
        )
        attempt_id = _handoff_attempt_id(handoff_id, 1)
        handoff = {
            "handoff_id": handoff_id,
            "record_revision": 1,
            "state": "setup-pending",
            "kind": kind,
            "project_id": request["project_id"],
            "tree_id": request["tree_id"],
            "source_topic_id": request["actor_topic_id"],
            "target_topic_id": target_topic_id,
            "target_slug": target_slug,
            "scope": scope,
            "work_snapshot": work_snapshot,
            "work_snapshot_bytes": snapshot_bytes,
            "authoritative_references": references,
            "authoritative_references_sha256": references_digest,
            "attempts": [],
            "creation_idempotency_key": request["idempotency_key"],
            "creation_fingerprint": _mutation_fingerprint(request),
            "creation_result_json": "",
            "initial_dependencies": request.get("initial_dependencies", []),
        }
        identity_envelope, payload_digest, payload_bytes = _handoff_payload(handoff, attempt_id)
        attempt = {
            "attempt_id": attempt_id,
            "attempt_number": 1,
            "state": "setup-pending",
            "binding_eligible": True,
            "payload_sha256": payload_digest,
            "handoff_payload_bytes": payload_bytes,
            "conversation_ref": None,
            "reason": None,
        }
        handoff["attempts"].append(attempt)
        records["Phase Runs"].append(
            {
                "run_id": handoff_id,
                "run_kind": "discussion-handoff",
                "state": "setup-pending",
                "record_revision": 1,
                "data_json": _canonical_json(handoff),
            }
        )
        if kind == "child":
            if any(topic.get("topic_id") == target_topic_id for topic in records["Current Topics"]):
                raise ProtocolError("handoff_identity_conflict", "child topic identity already exists")
            records["Current Topics"].append(
                {
                    "topic_id": target_topic_id,
                    "record_revision": 1,
                    "root_slug": target_slug,
                    "parent_topic_id": request["actor_topic_id"],
                    "current_phase": 0,
                    "phase_state": "setup-pending",
                    "review_state": "unreviewed",
                    "topic_state": "open",
                    "topic_document_path": None,
                }
            )
            records["Relations and Coverage"].append(
                {
                    "relation_id": f"REL-{handoff_id[2:]}",
                    "relation_type": "parent",
                    "source_topic_id": request["actor_topic_id"],
                    "target_topic_id": target_topic_id,
                    "state": "active",
                    "handoff_id": handoff_id,
                }
            )
            initial_dependencies = prepare_initial_dependencies(
                records, request=request, target_topic_id=target_topic_id,
                handoff_id=handoff_id, ledger_revision=ledger_revision + 1,
            )
        else:
            records["Relations and Coverage"].append(
                {
                    "relation_id": f"REL-{handoff_id[2:]}",
                    "relation_type": "continuation",
                    "source_topic_id": request["actor_topic_id"],
                    "target_topic_id": target_topic_id,
                    "state": "setup-pending",
                    "handoff_id": handoff_id,
                }
            )
            initial_dependencies = []
        next_revision = ledger_revision + 1
        result = {
            **_handoff_result(
                request, handoff, attempt, ledger_revision=next_revision, topic_revision=topic_revision
            ),
            "identity_envelope": identity_envelope,
            "work_snapshot_bytes": snapshot_bytes,
            "handoff_payload_bytes": payload_bytes,
            "initial_dependencies": initial_dependencies,
        }
        handoff["creation_result_json"] = _canonical_json(result)
        _store_handoff(
            _record_by_id(records["Phase Runs"], "run_id", handoff_id, "handoff_id"),
            handoff,
        )
        _write_ledger_transaction(
            ledger_path, frontmatter, records, request,
            ledger_revision=next_revision, event_type="handoff-prepared", result=result,
        )
        return result


def _handoff_mutation_context(
    request: dict[str, Any], allowed: set[str]
) -> tuple[Path, Path, Path, str]:
    _, ledger_path, _, lock_path, owner_ref = _evolution_paths(
        request, allow_tree_topic=True
    )
    _expect_keys(
        request,
        {
            "protocol_version", "operation", "project_path", "project_id", "tree_id",
            "actor_topic_id", "actor_conversation_ref", "expected_ledger_revision",
            "expected_topic_revision", "idempotency_key",
        } | allowed,
        f"{request['operation']} request",
    )
    _validate_uuid4(request["idempotency_key"], "idempotency_key")
    return ledger_path, lock_path, Path(request["project_path"]), owner_ref


def _bind_handoff(request: dict[str, Any]) -> dict[str, Any]:
    ledger_path, lock_path, _, owner_ref = _handoff_mutation_context(
        request, {"handoff_id", "attempt_id", "conversation_ref", "verified_identity"}
    )
    conversation_ref = _expect_string(request["conversation_ref"], "conversation_ref", max_bytes=1024)
    verified = request["verified_identity"]
    if not isinstance(verified, dict):
        raise ProtocolError("invalid_request", "verified_identity must be an object")
    _expect_keys(
        verified,
        {"project_id", "tree_id", "topic_id", "handoff_id", "attempt_id", "payload_sha256"},
        "verified_identity",
    )
    with lock_path.open("a+b") as lock_stream:
        _flock_with_timeout(lock_stream)
        frontmatter, records = _load_records(ledger_path)
        replay = _idempotent_result(records, request)
        if replay is not None:
            return replay
        source_topic = _record_by_id(
            records["Current Topics"], "topic_id", request["actor_topic_id"], "topic_id"
        )
        ledger_revision, topic_revision = _validate_revisions(request, frontmatter, source_topic)
        _verify_topic_owner(records, request["actor_topic_id"], owner_ref)
        record = _handoff_record(records, request["handoff_id"])
        handoff = _handoff_data(record)
        if handoff["source_topic_id"] != request["actor_topic_id"]:
            raise ProtocolError("handoff_identity_conflict", "handoff source topic does not match actor")
        attempt = _handoff_attempt(handoff, request["attempt_id"])
        if not attempt["binding_eligible"]:
            raise ProtocolError("handoff_late_arrival", "attempt binding eligibility was revoked")
        if attempt["state"] not in {"setup-pending", "outcome-unknown"}:
            raise ProtocolError("handoff_attempt_state_conflict", "attempt cannot be bound from its current state")
        expected_identity = {
            "project_id": request["project_id"],
            "tree_id": request["tree_id"],
            "topic_id": handoff["target_topic_id"],
            "handoff_id": handoff["handoff_id"],
            "attempt_id": attempt["attempt_id"],
            "payload_sha256": attempt["payload_sha256"],
        }
        if verified != expected_identity:
            raise ProtocolError("handoff_identity_conflict", "verified handoff identity does not match frozen intent")
        active = [
            binding for binding in records["Conversation Bindings"]
            if binding.get("topic_id") == handoff["target_topic_id"] and binding.get("binding_state") == "active"
        ]
        superseded = None
        if handoff["kind"] == "continuation":
            if len(active) != 1 or active[0].get("conversation_ref") != owner_ref:
                raise ProtocolError("handoff_attempt_state_conflict", "continuation source binding changed")
            superseded = active[0]["conversation_ref"]
            if conversation_ref == superseded:
                raise ProtocolError(
                    "handoff_identity_conflict",
                    "continuation must bind a new conversation reference",
                )
            prior_handoff_id = active[0].get("handoff_id")
            prior_attempt_id = active[0].get("attempt_id")
            if prior_handoff_id is not None or prior_attempt_id is not None:
                if not isinstance(prior_handoff_id, str) or not isinstance(prior_attempt_id, str):
                    raise ProtocolError("state_corrupt", "active continuation binding provenance is incomplete")
                prior_record = _handoff_record(records, prior_handoff_id)
                prior_handoff = _handoff_data(prior_record)
                prior_attempt = _handoff_attempt(prior_handoff, prior_attempt_id)
                if (
                    prior_handoff["target_topic_id"] != handoff["target_topic_id"]
                    or prior_attempt.get("conversation_ref") != superseded
                    or prior_attempt["state"] not in {
                        "bound-pending-acceptance", "accepted-awaiting-next-turn", "active"
                    }
                ):
                    raise ProtocolError("state_corrupt", "active continuation binding provenance is invalid")
                prior_attempt["state"] = "superseded"
                prior_handoff["state"] = "superseded"
                prior_handoff["record_revision"] += 1
                _store_handoff(prior_record, prior_handoff)
            active[0]["binding_state"] = "superseded"
            active[0]["superseded_by"] = conversation_ref
            relation = _record_by_id(
                records["Relations and Coverage"], "handoff_id", handoff["handoff_id"], "continuation relation"
            )
            relation["state"] = "active"
            relation["continuation_of"] = superseded
        elif active:
            raise ProtocolError("handoff_attempt_state_conflict", "child topic already has an active binding")
        records["Conversation Bindings"].append(
            {
                "topic_id": handoff["target_topic_id"],
                "conversation_ref": conversation_ref,
                "binding_state": "active",
                "record_revision": 1,
                "handoff_id": handoff["handoff_id"],
                "attempt_id": attempt["attempt_id"],
            }
        )
        attempt.update({"state": "bound-pending-acceptance", "conversation_ref": conversation_ref})
        handoff["state"] = "bound-pending-acceptance"
        handoff["record_revision"] += 1
        _store_handoff(record, handoff)
        next_revision = ledger_revision + 1
        result = {
            **_handoff_result(request, handoff, attempt, ledger_revision=next_revision, topic_revision=topic_revision),
            "conversation_ref": conversation_ref,
            "active_conversation_ref": conversation_ref,
            "superseded_conversation_ref": superseded,
        }
        _inject_failure("handoff-before-binding-ledger-write")
        _write_ledger_transaction(
            ledger_path, frontmatter, records, request,
            ledger_revision=next_revision, event_type="handoff-bound", result=result,
        )
        return result


def _accept_handoff(request: dict[str, Any]) -> dict[str, Any]:
    ledger_path, lock_path, _, owner_ref = _handoff_mutation_context(
        request,
        {"handoff_id", "attempt_id", "payload_sha256", "source_reference_sha256", "turn_number"},
    )
    if request["turn_number"] != 1:
        raise ProtocolError("handoff_identity_conflict", "handoff acceptance must occur in the first turn")
    with lock_path.open("a+b") as lock_stream:
        _flock_with_timeout(lock_stream)
        frontmatter, records = _load_records(ledger_path)
        replay = _idempotent_result(records, request)
        if replay is not None:
            return replay
        topic_record = _record_by_id(
            records["Current Topics"], "topic_id", request["actor_topic_id"], "topic_id"
        )
        ledger_revision, topic_revision = _validate_revisions(request, frontmatter, topic_record)
        _verify_topic_owner(records, request["actor_topic_id"], owner_ref)
        record = _handoff_record(records, request["handoff_id"])
        handoff = _handoff_data(record)
        attempt = _handoff_attempt(handoff, request["attempt_id"])
        if handoff["target_topic_id"] != request["actor_topic_id"] or attempt.get("conversation_ref") != owner_ref:
            raise ProtocolError("handoff_identity_conflict", "accepting conversation does not own the target topic")
        if attempt["state"] != "bound-pending-acceptance":
            raise ProtocolError("handoff_attempt_state_conflict", "attempt is not awaiting acceptance")
        if (
            request["payload_sha256"] != attempt["payload_sha256"]
            or request["source_reference_sha256"] != handoff["authoritative_references_sha256"]
        ):
            raise ProtocolError("handoff_identity_conflict", "handoff payload or source digest verification failed")
        attempt.update({"state": "accepted-awaiting-next-turn", "accepted_turn": 1})
        handoff["state"] = "accepted-awaiting-next-turn"
        handoff["record_revision"] += 1
        _store_handoff(record, handoff)
        next_revision = ledger_revision + 1
        result = {
            **_handoff_result(request, handoff, attempt, ledger_revision=next_revision, topic_revision=topic_revision),
            "substantive_discussion_allowed": False,
        }
        _write_ledger_transaction(
            ledger_path, frontmatter, records, request,
            ledger_revision=next_revision, event_type="handoff-accepted", result=result,
        )
        return result


def _authorize_handoff_discussion(request: dict[str, Any]) -> dict[str, Any]:
    ledger_path, lock_path, _, owner_ref = _handoff_mutation_context(
        request, {"handoff_id", "attempt_id", "turn_number"}
    )
    turn_number = request["turn_number"]
    if not isinstance(turn_number, int) or isinstance(turn_number, bool):
        raise ProtocolError("invalid_request", "turn_number must be an integer")
    with lock_path.open("a+b") as lock_stream:
        _flock_with_timeout(lock_stream)
        frontmatter, records = _load_records(ledger_path)
        replay = _idempotent_result(records, request)
        if replay is not None:
            return replay
        topic_record = _record_by_id(records["Current Topics"], "topic_id", request["actor_topic_id"], "topic_id")
        ledger_revision, topic_revision = _validate_revisions(request, frontmatter, topic_record)
        _verify_topic_owner(records, request["actor_topic_id"], owner_ref)
        require_open_gate(records, request["actor_topic_id"])
        record = _handoff_record(records, request["handoff_id"])
        handoff = _handoff_data(record)
        attempt = _handoff_attempt(handoff, request["attempt_id"])
        if attempt["state"] != "accepted-awaiting-next-turn":
            raise ProtocolError("handoff_attempt_state_conflict", "attempt is not awaiting a later turn")
        if handoff["target_topic_id"] != request["actor_topic_id"] or attempt.get("conversation_ref") != owner_ref:
            raise ProtocolError("handoff_identity_conflict", "discussion authorization actor is not the accepted target")
        if turn_number <= attempt["accepted_turn"]:
            raise ProtocolError("handoff_next_turn_required", "substantive discussion requires a later turn")
        attempt["state"] = "active"
        handoff["state"] = "active"
        handoff["record_revision"] += 1
        _store_handoff(record, handoff)
        next_revision = ledger_revision + 1
        result = {
            **_handoff_result(request, handoff, attempt, ledger_revision=next_revision, topic_revision=topic_revision),
            "substantive_discussion_allowed": True,
        }
        _write_ledger_transaction(
            ledger_path, frontmatter, records, request,
            ledger_revision=next_revision, event_type="handoff-discussion-authorized", result=result,
        )
        return result


def _transition_handoff_attempt(
    request: dict[str, Any], *, target_state: str, event_type: str
) -> dict[str, Any]:
    extra = {"handoff_id", "attempt_id"}
    if target_state in {"failed", "cancelled"}:
        extra.add("reason")
    ledger_path, lock_path, _, owner_ref = _handoff_mutation_context(request, extra)
    reason = (
        _expect_string(request["reason"], "reason", max_bytes=2048)
        if "reason" in extra
        else None
    )
    with lock_path.open("a+b") as lock_stream:
        _flock_with_timeout(lock_stream)
        frontmatter, records = _load_records(ledger_path)
        replay = _idempotent_result(records, request)
        if replay is not None:
            return replay
        topic_record = _record_by_id(records["Current Topics"], "topic_id", request["actor_topic_id"], "topic_id")
        ledger_revision, topic_revision = _validate_revisions(request, frontmatter, topic_record)
        _verify_topic_owner(records, request["actor_topic_id"], owner_ref)
        record = _handoff_record(records, request["handoff_id"])
        handoff = _handoff_data(record)
        if handoff["source_topic_id"] != request["actor_topic_id"]:
            raise ProtocolError("handoff_identity_conflict", "only the source topic may record creation outcomes")
        attempt = _handoff_attempt(handoff, request["attempt_id"])
        allowed_states = {
            "outcome-unknown": {"setup-pending"},
            "failed": {"setup-pending", "outcome-unknown"},
            "cancelled": {"setup-pending", "outcome-unknown", "failed"},
        }[target_state]
        if attempt["state"] not in allowed_states:
            raise ProtocolError("handoff_attempt_state_conflict", "attempt cannot enter requested state")
        attempt.update({"state": target_state, "reason": reason})
        if target_state in {"failed", "cancelled"}:
            attempt["binding_eligible"] = False
        handoff["state"] = target_state
        handoff["record_revision"] += 1
        _store_handoff(record, handoff)
        next_revision = ledger_revision + 1
        result = {
            **_handoff_result(request, handoff, attempt, ledger_revision=next_revision, topic_revision=topic_revision),
            "binding_eligible": attempt["binding_eligible"],
        }
        _write_ledger_transaction(
            ledger_path, frontmatter, records, request,
            ledger_revision=next_revision, event_type=event_type, result=result,
        )
        return result


def _retry_handoff(request: dict[str, Any]) -> dict[str, Any]:
    allowed = {"handoff_id", "prior_attempt_id", "forced"}
    if request.get("forced") is True:
        allowed.add("user_authorization")
    ledger_path, lock_path, _, owner_ref = _handoff_mutation_context(request, allowed)
    forced = request["forced"]
    if not isinstance(forced, bool):
        raise ProtocolError("invalid_request", "forced must be boolean")
    if forced:
        _expect_string(request["user_authorization"], "user_authorization", max_bytes=2048)
    with lock_path.open("a+b") as lock_stream:
        _flock_with_timeout(lock_stream)
        frontmatter, records = _load_records(ledger_path)
        replay = _idempotent_result(records, request)
        if replay is not None:
            return replay
        topic_record = _record_by_id(records["Current Topics"], "topic_id", request["actor_topic_id"], "topic_id")
        ledger_revision, topic_revision = _validate_revisions(request, frontmatter, topic_record)
        _verify_topic_owner(records, request["actor_topic_id"], owner_ref)
        record = _handoff_record(records, request["handoff_id"])
        handoff = _handoff_data(record)
        if handoff["source_topic_id"] != request["actor_topic_id"]:
            raise ProtocolError("handoff_identity_conflict", "only the source topic may retry creation")
        prior = _handoff_attempt(handoff, request["prior_attempt_id"])
        if prior is not handoff["attempts"][-1]:
            raise ProtocolError("handoff_attempt_state_conflict", "only the latest attempt may be retried")
        if prior["state"] == "outcome-unknown" and not forced:
            raise ProtocolError("handoff_reconciliation_required", "outcome-unknown attempt must be reconciled first")
        if prior["state"] not in {"failed", "cancelled", "outcome-unknown"}:
            raise ProtocolError("handoff_attempt_state_conflict", "attempt is not terminal or uncertain")
        if not forced and prior["state"] != "failed":
            raise ProtocolError("handoff_reconciliation_required", "retry requires explicit failure or user authorization")
        prior["binding_eligible"] = False
        number = len(handoff["attempts"]) + 1
        attempt_id = _handoff_attempt_id(handoff["handoff_id"], number)
        _, payload_digest, payload_bytes = _handoff_payload(handoff, attempt_id)
        attempt = {
            "attempt_id": attempt_id,
            "attempt_number": number,
            "state": "setup-pending",
            "binding_eligible": True,
            "payload_sha256": payload_digest,
            "handoff_payload_bytes": payload_bytes,
            "conversation_ref": None,
            "reason": None,
        }
        handoff["attempts"].append(attempt)
        handoff["state"] = "setup-pending"
        handoff["record_revision"] += 1
        _store_handoff(record, handoff)
        next_revision = ledger_revision + 1
        result = _handoff_result(
            request, handoff, attempt, ledger_revision=next_revision, topic_revision=topic_revision
        )
        _write_ledger_transaction(
            ledger_path, frontmatter, records, request,
            ledger_revision=next_revision, event_type="handoff-retried", result=result,
        )
        return result


def _reconcile_handoff_attempt(request: dict[str, Any]) -> dict[str, Any]:
    allowed = {"handoff_id", "attempt_id", "outcome"}
    if request.get("outcome") == "not-created":
        allowed.add("reason")
    ledger_path, lock_path, _, owner_ref = _handoff_mutation_context(request, allowed)
    outcome = _expect_string(request["outcome"], "outcome", max_bytes=32)
    if outcome not in {"still-unknown", "not-created"}:
        raise ProtocolError("invalid_request", "reconciliation outcome is unsupported")
    reason = _expect_string(request["reason"], "reason", max_bytes=2048) if outcome == "not-created" else None
    with lock_path.open("a+b") as lock_stream:
        _flock_with_timeout(lock_stream)
        frontmatter, records = _load_records(ledger_path)
        replay = _idempotent_result(records, request)
        if replay is not None:
            return replay
        topic_record = _record_by_id(records["Current Topics"], "topic_id", request["actor_topic_id"], "topic_id")
        ledger_revision, topic_revision = _validate_revisions(request, frontmatter, topic_record)
        _verify_topic_owner(records, request["actor_topic_id"], owner_ref)
        record = _handoff_record(records, request["handoff_id"])
        handoff = _handoff_data(record)
        attempt = _handoff_attempt(handoff, request["attempt_id"])
        if handoff["source_topic_id"] != request["actor_topic_id"] or attempt["state"] != "outcome-unknown":
            raise ProtocolError("handoff_attempt_state_conflict", "attempt is not reconcilable by this topic")
        if outcome == "not-created":
            attempt.update({"state": "failed", "binding_eligible": False, "reason": reason})
            handoff["state"] = "failed"
        handoff["record_revision"] += 1
        _store_handoff(record, handoff)
        next_revision = ledger_revision + 1
        result = {
            **_handoff_result(request, handoff, attempt, ledger_revision=next_revision, topic_revision=topic_revision),
            "reconciliation_outcome": outcome,
            "binding_eligible": attempt["binding_eligible"],
        }
        _write_ledger_transaction(
            ledger_path, frontmatter, records, request,
            ledger_revision=next_revision, event_type="handoff-reconciled", result=result,
        )
        return result


def _submit_child_result(request: dict[str, Any]) -> dict[str, Any]:
    extra = {"handoff_id", "attempt_id", "result_scope", "summary"}
    if "authority_selection" in request: extra.add("authority_selection")
    ledger_path, lock_path, _, owner_ref = _handoff_mutation_context(request, extra)
    result_scope = _validated_string_list(request["result_scope"], "result_scope")
    summary = _expect_string(request["summary"], "summary", max_bytes=4096)
    with lock_path.open("a+b") as lock_stream:
        _flock_with_timeout(lock_stream)
        frontmatter, records = _load_records(ledger_path)
        replay = _idempotent_result(records, request)
        if replay is not None:
            return replay
        topic_record = _record_by_id(records["Current Topics"], "topic_id", request["actor_topic_id"], "topic_id")
        ledger_revision, topic_revision = _validate_revisions(request, frontmatter, topic_record)
        _verify_topic_owner(records, request["actor_topic_id"], owner_ref)
        record = _handoff_record(records, request["handoff_id"])
        handoff = _handoff_data(record)
        if (
            handoff["kind"] != "child"
            or handoff["target_topic_id"] != request["actor_topic_id"]
        ):
            raise ProtocolError(
                "child_result_state_conflict",
                "result does not originate from this child topic",
            )
        active_binding = [
            binding for binding in records["Conversation Bindings"]
            if binding.get("topic_id") == request["actor_topic_id"]
            and binding.get("conversation_ref") == owner_ref
            and binding.get("binding_state") == "active"
        ]
        if len(active_binding) != 1 or active_binding[0].get("attempt_id") != request["attempt_id"]:
            raise ProtocolError(
                "child_result_state_conflict",
                "result attempt is not the current active child binding",
            )
        provenance_handoff = _handoff_data(
            _handoff_record(records, active_binding[0].get("handoff_id"))
        )
        attempt = _handoff_attempt(provenance_handoff, request["attempt_id"])
        if (
            provenance_handoff["target_topic_id"] != request["actor_topic_id"]
            or attempt["state"] != "active"
            or attempt.get("conversation_ref") != owner_ref
        ):
            raise ProtocolError(
                "child_result_state_conflict",
                "only the active accepted child attempt may submit a result",
            )
        seed = uuid.UUID(request["idempotency_key"]).hex
        child_result_id = f"CR-{seed}"
        next_revision = ledger_revision + 1
        selection = request.get("authority_selection", {"authority_kind": "confirmed-decision", "authority_identity": None, "decision_ids": []})
        frozen_authority = freeze_authority_selection(
            records, request["actor_topic_id"], selection
        )
        claim = {
            "result_id": child_result_id,
            "result_kind": "child-topic-result",
            "handoff_id": handoff["handoff_id"],
            "attempt_id": attempt["attempt_id"],
            "provenance_handoff_id": provenance_handoff["handoff_id"],
            "source_topic_id": handoff["target_topic_id"],
            "target_topic_id": handoff["source_topic_id"],
            "result_scope_json": _canonical_json(result_scope),
            "summary": summary,
            "authority_json": _canonical_json(frozen_authority),
            "state": "pending",
            "record_revision": 1,
        }
        records["Phase Results"].append(claim)
        result = {
            "ok": True, "state": "pending-parent-acceptance", "idempotent_replay": False,
            "project_id": request["project_id"], "tree_id": request["tree_id"],
            "topic_id": request["actor_topic_id"], "ledger_revision": next_revision,
            "record_revision": topic_revision, "handoff_id": handoff["handoff_id"],
            "attempt_id": attempt["attempt_id"], "child_result_id": child_result_id,
        }
        _write_ledger_transaction(
            ledger_path, frontmatter, records, request,
            ledger_revision=next_revision, event_type="child-result-submitted", result=result,
        )
        return result


def _record_child_result(request: dict[str, Any]) -> dict[str, Any]:
    extra = {"handoff_id", "child_result_id", "effect"}
    if "dependency_releases" in request:
        extra.add("dependency_releases")
    ledger_path, lock_path, _, owner_ref = _handoff_mutation_context(request, extra)
    effect = _expect_string(request["effect"], "effect", max_bytes=32)
    if effect not in {"absorb", "impact"}:
        raise ProtocolError("invalid_request", "effect must be absorb or impact")
    with lock_path.open("a+b") as lock_stream:
        _flock_with_timeout(lock_stream)
        frontmatter, records = _load_records(ledger_path)
        replay = _idempotent_result(records, request)
        if replay is not None:
            return replay
        topic_record = _record_by_id(records["Current Topics"], "topic_id", request["actor_topic_id"], "topic_id")
        ledger_revision, topic_revision = _validate_revisions(request, frontmatter, topic_record)
        _verify_topic_owner(records, request["actor_topic_id"], owner_ref)
        record = _handoff_record(records, request["handoff_id"])
        handoff = _handoff_data(record)
        if handoff["kind"] != "child" or handoff["source_topic_id"] != request["actor_topic_id"]:
            raise ProtocolError("handoff_identity_conflict", "result is not owned by this child handoff parent")
        claim = _record_by_id(
            records["Phase Results"], "result_id",
            _expect_string(request["child_result_id"], "child_result_id", max_bytes=64),
            "child_result_id",
        )
        if (
            claim.get("result_kind") != "child-topic-result"
            or claim.get("handoff_id") != handoff["handoff_id"]
            or claim.get("source_topic_id") != handoff["target_topic_id"]
            or claim.get("target_topic_id") != request["actor_topic_id"]
            or claim.get("state") != "pending"
        ):
            raise ProtocolError("child_result_state_conflict", "child result claim is not pending for this parent")
        result_scope = _json_field(claim, "result_scope_json", "child result")
        summary = claim["summary"]
        within_scope = set(result_scope).issubset(set(handoff["scope"]))
        if effect == "absorb" and not within_scope:
            raise ProtocolError("impact_state_conflict", "cross-topic result cannot be silently absorbed")
        releases = request.get("dependency_releases", [])
        if effect != "absorb" and releases:
            raise ProtocolError("topic_dependency_state_conflict", "only an absorbed child result can release a gate")
        if not isinstance(releases, list) or len(releases) > 64:
            raise ProtocolError("invalid_request", "dependency_releases must be a bounded list")
        frozen = _json_field(claim, "authority_json", "child result authority")
        frozen_kind = frozen.get("authority_kind")
        frozen_identity = frozen.get("authority_identity")
        if frozen_kind == "phase-0-checkpoint":
            matches = [item for item in records["Checkpoints"] if item.get("checkpoint_id") == frozen_identity]
            if len(matches) != 1 or matches[0].get("topic_id") != handoff["target_topic_id"] or matches[0].get("state") != "completed":
                raise ProtocolError("topic_dependency_evidence_unavailable", "frozen checkpoint authority is no longer current")
        if frozen_kind == "phase-1-result":
            matches = [item for item in records["Phase Results"] if item.get("result_id") == frozen_identity]
            if len(matches) != 1 or matches[0].get("result_kind") != "phase-result" or matches[0].get("state") != "completed" or _json_field(matches[0], "data_json", "phase result").get("topic_id") != handoff["target_topic_id"]:
                raise ProtocolError("topic_dependency_evidence_unavailable", "frozen phase-result authority is no longer current")
        frozen_decisions = {item.get("decision_id"): item for item in frozen.get("decision_authority", []) if isinstance(item, dict)}
        selected_dependencies = []
        for release in releases:
            if not isinstance(release, dict) or set(release) != {"dependency_id", "decision_ids", "authority_kind", "authority_identity"}:
                raise ProtocolError("invalid_request", "dependency release is invalid")
            dependency = _record_by_id(records["Topic Dependencies"], "dependency_id", release["dependency_id"], "dependency_id")
            ids = release["decision_ids"]
            expected_kind = {"confirmed-decision": "confirmed-decision", "phase-0-checkpoint": "phase-0-checkpoint", "phase-1-result": "phase-1-result"}.get(dependency.get("requirement_kind"))
            if (release["authority_kind"] != frozen_kind or release["authority_identity"] != frozen_identity or expected_kind != frozen_kind or dependency.get("dependent_topic_id") != request["actor_topic_id"] or dependency.get("prerequisite_topic_id") != handoff["target_topic_id"] or dependency.get("relation_state") != "active" or dependency.get("gate_state") != "closed" or not isinstance(ids, list) or sorted(set(ids)) != ids or any(item not in frozen_decisions for item in ids)):
                raise ProtocolError("topic_dependency_state_conflict", "child result does not match a current closed dependency")
            selected_dependencies.append((dependency, ids))
        seed = uuid.UUID(request["idempotency_key"]).hex
        next_revision = ledger_revision + 1
        if effect == "absorb":
            relation_id = f"REL-{seed}"
            records["Relations and Coverage"].append(
                {
                    "relation_id": relation_id,
                    "relation_type": "absorbs",
                    "source_topic_id": request["actor_topic_id"],
                    "target_topic_id": handoff["target_topic_id"],
                    "scope_json": _canonical_json(result_scope),
                    "summary": summary,
                    "state": "active",
                    "handoff_id": handoff["handoff_id"],
                }
            )
            result = {
                "ok": True, "state": "absorbed", "idempotent_replay": False,
                "project_id": request["project_id"], "tree_id": request["tree_id"],
                "topic_id": request["actor_topic_id"], "ledger_revision": next_revision,
                "record_revision": topic_revision, "handoff_id": handoff["handoff_id"],
                "relation_id": relation_id,
            }
            claim["state"] = "absorbed"
            for dependency, ids in selected_dependencies:
                authority = [{"decision_id": item, "sha256": _sha256(_canonical_json(frozen_decisions[item]).encode("utf-8"))} for item in ids]
                dependency["gate_state"] = "open"
                dependency["record_revision"] += 1
                basis = {"basis_version": 1, "dependency_id": dependency["dependency_id"], "prerequisite_topic_id": handoff["target_topic_id"], "requirement_kind": frozen_kind, "child_result_id": claim["result_id"], "decision_authority": authority}
                if frozen_identity is not None: basis["authority"] = {"result_id" if frozen_kind == "phase-1-result" else "checkpoint_id": frozen_identity}
                dependency["accepted_basis_json"] = _canonical_json(basis)
                dependency["gate_reason_json"] = _canonical_json({"kind": "child-result-absorb-release", "ledger_revision": next_revision, "child_result_id": claim["result_id"]})
            result["released_dependency_ids"] = sorted(item[0]["dependency_id"] for item in selected_dependencies)
        else:
            impact_id = f"IMP-{seed}"
            impact = {
                "impact_id": impact_id,
                "source_topic_id": handoff["target_topic_id"],
                "target_topic_id": request["actor_topic_id"],
                "scope": result_scope,
                "summary": summary,
                "state": "pending",
                "handoff_id": handoff["handoff_id"],
            }
            records["Impacts"].append(
                {"impact_id": impact_id, "topic_id": request["actor_topic_id"], "data_json": _canonical_json(impact)}
            )
            result = {
                "ok": True, "state": "pending-impact", "idempotent_replay": False,
                "project_id": request["project_id"], "tree_id": request["tree_id"],
                "topic_id": request["actor_topic_id"], "ledger_revision": next_revision,
                "record_revision": topic_revision, "handoff_id": handoff["handoff_id"],
                "impact_id": impact_id,
            }
            claim["state"] = "impact-recorded"
        claim["record_revision"] += 1
        _write_ledger_transaction(
            ledger_path, frontmatter, records, request,
            ledger_revision=next_revision, event_type=f"child-result-{result['state']}", result=result,
        )
        return result


def _read_handoff(request: dict[str, Any]) -> dict[str, Any]:
    _, ledger_path, _, lock_path, owner_ref = _evolution_paths(
        request, query=True, allow_tree_topic=True
    )
    _expect_keys(
        request,
        {"protocol_version", "operation", "project_path", "project_id", "tree_id", "actor_topic_id", "actor_conversation_ref", "handoff_id"},
        "read-handoff request",
    )
    with lock_path.open("a+b") as lock_stream:
        _flock_with_timeout(lock_stream)
        frontmatter, records = _load_records(ledger_path)
        handoff = _handoff_data(_handoff_record(records, request["handoff_id"]))
        if request["actor_topic_id"] not in {handoff["source_topic_id"], handoff["target_topic_id"]}:
            raise ProtocolError("handoff_identity_conflict", "topic is outside this handoff")
        authorized_bindings = [
            binding for binding in records["Conversation Bindings"]
            if binding.get("topic_id") == request["actor_topic_id"]
            and binding.get("conversation_ref") == owner_ref
            and binding.get("binding_state") in {"active", "superseded"}
        ]
        if len(authorized_bindings) != 1:
            raise ProtocolError(
                "document_ownership_conflict",
                "the caller is not a verified participant in this handoff topic",
            )
        target_topic = _record_by_id(
            records["Current Topics"], "topic_id", handoff["target_topic_id"], "target_topic_id"
        )
        bindings = [
            dict(binding) for binding in records["Conversation Bindings"]
            if binding.get("topic_id") == handoff["target_topic_id"]
        ]
        return {
            "ok": True,
            "state": "read",
            "ledger_revision": int(frontmatter["ledger_revision"]),
            "record_revision": target_topic["record_revision"],
            "handoff": {key: value for key, value in handoff.items() if key != "attempts"},
            "attempts": [dict(attempt) for attempt in handoff["attempts"]],
            "target_topic": dict(target_topic),
            "bindings": bindings,
        }


def _validate_handoffs(records: dict[str, list[dict[str, Any]]]) -> int:
    handoffs = [
        record for record in records["Phase Runs"]
        if record.get("run_kind") == "discussion-handoff"
    ]
    active_by_topic: dict[str, int] = {}
    for binding in records["Conversation Bindings"]:
        if binding.get("binding_state") == "active":
            topic_id = binding.get("topic_id")
            active_by_topic[topic_id] = active_by_topic.get(topic_id, 0) + 1
    if any(count != 1 for count in active_by_topic.values()):
        raise ProtocolError("state_corrupt", "a topic has multiple active conversation bindings")
    for record in handoffs:
        handoff = _handoff_data(record)
        if (
            handoff.get("kind") not in HANDOFF_KINDS
            or handoff.get("project_id") is None
            or handoff.get("tree_id") is None
            or handoff.get("authoritative_references_sha256")
            != _sha256(_canonical_json(handoff.get("authoritative_references")).encode("utf-8"))
        ):
            raise ProtocolError("state_corrupt", "handoff frozen identity or references are invalid")
        attempts = handoff.get("attempts")
        if not isinstance(attempts, list) or not attempts:
            raise ProtocolError("state_corrupt", "handoff attempt history is empty")
        eligible = 0
        for number, attempt in enumerate(attempts, start=1):
            if (
                attempt.get("attempt_number") != number
                or attempt.get("attempt_id") != _handoff_attempt_id(handoff["handoff_id"], number)
            ):
                raise ProtocolError("state_corrupt", "handoff attempt history is not unique and ordered")
            _, payload_digest, payload_bytes = _handoff_payload(handoff, attempt["attempt_id"])
            if (
                attempt.get("payload_sha256") != payload_digest
                or attempt.get("handoff_payload_bytes") != payload_bytes
            ):
                raise ProtocolError("state_corrupt", "handoff attempt payload digest is invalid")
            if attempt.get("binding_eligible") is True:
                eligible += 1
        if eligible > 1:
            raise ProtocolError("state_corrupt", "handoff has multiple binding-eligible attempts")
        bound_attempts = [attempt for attempt in attempts if attempt.get("conversation_ref")]
        for bound_attempt in bound_attempts:
            attempt_bindings = [
                binding for binding in records["Conversation Bindings"]
                if binding.get("topic_id") == handoff["target_topic_id"]
                and binding.get("handoff_id") == handoff["handoff_id"]
                and binding.get("attempt_id") == bound_attempt["attempt_id"]
                and binding.get("conversation_ref") == bound_attempt["conversation_ref"]
                and binding.get("binding_state") in {"active", "superseded"}
            ]
            if len(attempt_bindings) != 1:
                raise ProtocolError(
                    "state_corrupt", "bound handoff attempt does not have one traceable binding"
                )
        if handoff["state"] in {"bound-pending-acceptance", "accepted-awaiting-next-turn", "active"}:
            current = bound_attempts[-1] if bound_attempts else None
            current_bindings = [
                binding for binding in records["Conversation Bindings"]
                if current is not None
                and binding.get("topic_id") == handoff["target_topic_id"]
                and binding.get("handoff_id") == handoff["handoff_id"]
                and binding.get("attempt_id") == current["attempt_id"]
                and binding.get("binding_state") == "active"
            ]
            if len(current_bindings) != 1:
                raise ProtocolError("state_corrupt", "current handoff does not own its active binding")
    return len(handoffs)
