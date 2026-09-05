"""Dependency-free schema checks for Topic Dependency ledger records."""

from __future__ import annotations

import json
import hashlib
import re
from dataclasses import dataclass
from typing import Any, Callable


@dataclass(frozen=True)
class AuthorityDescriptor:
    """One fixed authority-kind strategy shared by schema and discovery."""
    kind: str
    candidate_key: str
    identity_field: str | None
    requires_decisions: bool
    authority_is_valid: Callable[[Any, list[dict[str, str]]], bool]
    authority_for_selection: Callable[[dict[str, Any], list[dict[str, str]]], dict[str, Any] | None]
    retained_provenance_is_valid: Callable[
        [dict[str, list[dict[str, Any]]], str, dict[str, Any], list[dict[str, str]], Callable[[str, str], None]],
        None,
    ]

    def validate_authority(self, authority: Any, decisions: list[dict[str, str]]) -> bool:
        """Validate this kind's exact persisted authority shape."""
        return self.authority_is_valid(authority, decisions)

    def validate_retained_provenance(
        self,
        records: dict[str, list[dict[str, Any]]],
        prerequisite_topic_id: str,
        authority: dict[str, Any],
        decisions: list[dict[str, str]],
        error: Callable[[str, str], None],
    ) -> None:
        """Resolve this kind's historical basis against retained records."""
        self.retained_provenance_is_valid(
            records, prerequisite_topic_id, authority, decisions, error
        )

    def selection_from_basis(self, basis: dict[str, Any]) -> dict[str, Any]:
        """Recover the canonical release selection from this kind's basis."""
        authority = basis.get("authority")
        selection = {
            "dependency_id": basis.get("dependency_id"),
            "decision_ids": [
                entry["decision_id"]
                for entry in basis.get("decision_authority", [])
                if isinstance(entry, dict) and isinstance(entry.get("decision_id"), str)
            ],
        }
        if self.identity_field is not None:
            selection["authority_id"] = (
                authority.get(self.identity_field) if isinstance(authority, dict) else None
            )
        return selection

    def basis_from_candidate(
        self,
        candidate: dict[str, Any],
        *,
        dependency_id: str,
        prerequisite_topic_id: str,
        decision_authority: list[dict[str, str]],
        child_result_id: str | None = None,
    ) -> dict[str, Any]:
        """Convert this kind's current candidate into one persisted basis."""
        basis: dict[str, Any] = {
            "basis_version": 1,
            "dependency_id": dependency_id,
            "prerequisite_topic_id": prerequisite_topic_id,
            "requirement_kind": self.kind,
            "decision_authority": decision_authority,
        }
        if child_result_id is not None:
            basis["child_result_id"] = child_result_id
        authority = self.authority_for_selection(candidate, decision_authority)
        if authority is not None:
            basis["authority"] = authority
        return basis


AUTHORITY_DESCRIPTORS: dict[str, AuthorityDescriptor] = {}
DEPENDENCY_AUTHORITY_KINDS = frozenset({
    "confirmed-decision", "phase-0-checkpoint", "phase-1-result",
})
MAX_AUTHORITY_DECISIONS = 64
RECORD_FIELDS = {
    "dependency_id", "record_revision", "dependent_topic_id", "prerequisite_topic_id",
    "requirement_kind", "requirement_summary", "relation_state", "gate_state",
    "accepted_basis_json", "gate_reason_json",
}
SHA256_RE = re.compile(r"[0-9a-f]{64}")
IDENTITY_RE = re.compile(r"(?:DEP|CP|PH|CR|H|DW)-[0-9a-f]{32}")
UUID4_RE = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}")


@dataclass(frozen=True)
class AuthoritySelection:
    """One normalized authority choice shared by child and gate flows."""

    authority_kind: str
    authority_identity: str | None
    decision_ids: tuple[str, ...]

    @property
    def descriptor(self) -> AuthorityDescriptor:
        """The single kind strategy used by parsing and basis construction."""
        return authority_descriptor(self.authority_kind)

    def as_request_fields(self) -> dict[str, Any]:
        """Return the canonical selection shape used for currentness checks."""
        return {
            "authority_kind": self.authority_kind,
            "authority_identity": self.authority_identity,
            "decision_ids": list(self.decision_ids),
        }

    @classmethod
    def parse(cls, value: Any, error: Callable[[str, str], None]) -> "AuthoritySelection":
        if not isinstance(value, dict) or set(value) != {
            "authority_kind", "authority_identity", "decision_ids",
        }:
            error("invalid_request", "authority_selection is invalid")
        kind = value["authority_kind"]
        identity = value["authority_identity"]
        ids = value["decision_ids"]
        if (
            not isinstance(kind, str)
            or kind not in DEPENDENCY_AUTHORITY_KINDS
            or not canonical_string_array(ids)
        ):
            error("invalid_request", "authority_selection is invalid")
        if authority_descriptor(kind).identity_field is None:
            if identity is not None:
                error("invalid_request", "confirmed-decision has no authority identity")
        elif not isinstance(identity, str):
            error("invalid_request", "authority identity is required")
        return cls(kind, identity, tuple(ids))


def authority_descriptor(kind: str) -> AuthorityDescriptor:
    """Return the authority strategy for one supported requirement kind."""
    if not isinstance(kind, str) or kind not in DEPENDENCY_AUTHORITY_KINDS:
        raise ValueError("unsupported dependency authority kind")
    return AUTHORITY_DESCRIPTORS[kind]


def decision_authority(
    decisions: list[dict[str, Any]], *, relevant_decision_ids: set[str] | None = None,
) -> tuple[list[dict[str, str]], dict[str, str], str]:
    """Normalize and hash decision authority without depending on ledger owners."""
    ordered = sorted(decisions, key=lambda item: item["decision_id"])
    digests = {
        item["decision_id"]: hashlib.sha256(
            json.dumps(item, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
        ).hexdigest()
        for item in ordered
    }
    relevant = relevant_decision_ids if relevant_decision_ids is not None else {
        item["decision_id"] for item in ordered if item.get("state") == "confirmed"
    }
    descriptor = [
        {"decision_id": item["decision_id"], "sha256": digests[item["decision_id"]]}
        for item in ordered if item["decision_id"] in relevant
    ]
    normalized = [
        {
            "decision_id": item["decision_id"],
            "evolution": item.get("evolution"),
            "rationale": item.get("rationale"),
            "state": item.get("state"),
            "summary": item["summary"],
        }
        for item in ordered
    ]
    digest = hashlib.sha256(
        json.dumps(normalized, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()
    return descriptor, digests, digest


def decision_pair_digest(decisions: list[dict[str, Any]]) -> str:
    """Hash the canonical exact decision pairs retained in an authority basis."""
    pairs = [
        {"decision_id": item["decision_id"], "sha256": item["sha256"]}
        for item in decisions
    ]
    return hashlib.sha256(
        json.dumps(pairs, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()


def canonical_object(value: Any, label: str, error: Callable[[str, str], None]) -> dict[str, Any]:
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


def _identity(value: Any, prefix: str) -> bool:
    if not isinstance(value, str):
        return False
    return (
        bool(re.fullmatch(r"PH-[0-9]{8}", value))
        if prefix == "PH"
        else bool(IDENTITY_RE.fullmatch(value)) and value.startswith(prefix + "-")
    )


def canonical_string_array(
    value: Any, *, maximum: int = MAX_AUTHORITY_DECISIONS,
) -> bool:
    return (
        isinstance(value, list)
        and len(value) <= maximum
        and all(isinstance(item, str) and item for item in value)
        and value == sorted(value)
        and len(set(value)) == len(value)
    )


def _decision_pairs(value: Any) -> bool:
    return (
        isinstance(value, list)
        and len(value) <= MAX_AUTHORITY_DECISIONS
        and all(
            isinstance(entry, dict)
            and set(entry) == {"decision_id", "sha256"}
            and isinstance(entry["decision_id"], str)
            and entry["decision_id"]
            and isinstance(entry["sha256"], str)
            and SHA256_RE.fullmatch(entry["sha256"])
            for entry in value
        )
        and value == sorted(value, key=lambda entry: entry["decision_id"])
        and len({entry["decision_id"] for entry in value}) == len(value)
    )


def _gate_reason(value: Any, error: Callable[[str, str], None]) -> None:
    reason = canonical_object(value, "topic dependency gate_reason_json", error)
    kind = reason.get("kind")
    if type(reason.get("ledger_revision")) is not int or reason["ledger_revision"] < 1:
        error("state_corrupt", "topic dependency gate reason revision is invalid")
    exact: dict[str, set[str]] = {
        "initial-handoff": {"kind", "handoff_id", "ledger_revision"},
        "explicit-create": {"kind", "dependency_update_id", "ledger_revision"},
        "explicit-replace": {"kind", "dependency_update_id", "ledger_revision"},
        "explicit-cancel": {"kind", "dependency_update_id", "ledger_revision"},
        "atomic-release": {"kind", "release_id", "ledger_revision"},
        "child-result-absorb-release": {"kind", "absorb_operation_id", "ledger_revision", "child_result_id"},
    }
    if kind in exact:
        if set(reason) != exact[kind]:
            error("state_corrupt", "topic dependency gate reason fields are incoherent")
        identity_field = {
            "initial-handoff": "handoff_id", "explicit-create": "dependency_update_id",
            "explicit-replace": "dependency_update_id", "explicit-cancel": "dependency_update_id",
            "atomic-release": "release_id", "child-result-absorb-release": "absorb_operation_id",
        }[kind]
        if kind == "initial-handoff":
            valid = _identity(reason[identity_field], "H")
        else:
            valid = isinstance(reason[identity_field], str) and bool(UUID4_RE.fullmatch(reason[identity_field]))
        if kind == "child-result-absorb-release":
            valid = valid and _identity(reason["child_result_id"], "CR")
        if not valid:
            error("state_corrupt", "topic dependency gate reason identity is invalid")
        return
    if kind != "direct-upstream-invalidation":
        error("state_corrupt", "topic dependency gate reason kind is invalid")
    cause_kinds = {
        "topic-update": ({"kind", "ledger_revision", "topic_update_id", "decision_id", "action"}, "topic_update_id"),
        "phase-reopen": ({"kind", "ledger_revision", "reopen_id", "affected_decision_ids", "invalidated_result_ids"}, "reopen_id"),
        "checkpoint-broken": ({"kind", "ledger_revision", "checkpoint_id", "checkpoint_broken_id", "broken_identity"}, "checkpoint_broken_id"),
        "checkpoint-superseded": ({"kind", "ledger_revision", "checkpoint_id", "checkpoint_supersession_id", "superseded_checkpoint_ids"}, "checkpoint_supersession_id"),
    }
    cause = next((item for item in cause_kinds.values() if item[1] in reason), None)
    if cause is None or set(reason) != cause[0]:
        error("state_corrupt", "topic dependency invalidation reason is incoherent")
    if cause[1] == "topic_update_id":
        valid_identity = _identity(reason[cause[1]], "DW")
    else:
        valid_identity = isinstance(reason[cause[1]], str) and bool(UUID4_RE.fullmatch(reason[cause[1]]))
    if not valid_identity:
        error("state_corrupt", "topic dependency invalidation identity is invalid")
    if "decision_id" in reason and (not isinstance(reason["decision_id"], str) or not reason["decision_id"] or reason.get("action") not in {"adjust", "replace", "discard"}):
        error("state_corrupt", "topic dependency update cause is invalid")
    if "checkpoint_id" in reason and not _identity(reason["checkpoint_id"], "CP"):
        error("state_corrupt", "topic dependency checkpoint identity is invalid")
    if "invalidated_result_ids" in reason and (
        not canonical_string_array(reason["invalidated_result_ids"])
        or any(not _identity(item, "PH") for item in reason["invalidated_result_ids"])
    ):
        error("state_corrupt", "topic dependency invalidated result identities are invalid")
    if "affected_decision_ids" in reason and (
        not canonical_string_array(reason["affected_decision_ids"])
    ):
        error("state_corrupt", "topic dependency affected decisions are invalid")
    if "superseded_checkpoint_ids" in reason and (
        not canonical_string_array(reason["superseded_checkpoint_ids"])
        or any(not _identity(item, "CP") for item in reason["superseded_checkpoint_ids"])
    ):
        error("state_corrupt", "topic dependency superseded checkpoint identities are invalid")


def _ledger_decision_pairs(
    records: dict[str, list[dict[str, Any]]], topic_id: str, error: Callable[[str, str], None],
) -> dict[str, str]:
    pairs: dict[str, str] = {}
    for record in records["Pending Items"]:
        if record.get("topic_id") != topic_id or record.get("item_kind") != "decision":
            continue
        decision = canonical_object(record.get("data_json"), "decision data_json", error)
        decision_id = decision.get("decision_id")
        if not isinstance(decision_id, str) or record.get("item_id") != decision_id:
            error("state_corrupt", "decision envelope is incoherent")
        pairs[decision_id] = hashlib.sha256(
            json.dumps(decision, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
        ).hexdigest()
    return pairs


def _validate_retained_decisions(
    records: dict[str, list[dict[str, Any]]], prerequisite_topic_id: str,
    decisions: list[dict[str, str]], error: Callable[[str, str], None],
) -> None:
    decision_pairs = _ledger_decision_pairs(records, prerequisite_topic_id, error)
    if any(decision_pairs.get(entry["decision_id"]) != entry["sha256"] for entry in decisions):
        error("state_corrupt", "topic dependency decision authority is not retained")


def _validate_confirmed_decision_provenance(
    records: dict[str, list[dict[str, Any]]], prerequisite_topic_id: str,
    authority: dict[str, Any], decisions: list[dict[str, str]],
    error: Callable[[str, str], None],
) -> None:
    _validate_retained_decisions(records, prerequisite_topic_id, decisions, error)


def _validate_checkpoint_provenance(
    records: dict[str, list[dict[str, Any]]], prerequisite_topic_id: str,
    authority: dict[str, Any], decisions: list[dict[str, str]],
    error: Callable[[str, str], None],
) -> None:
    _validate_retained_decisions(records, prerequisite_topic_id, decisions, error)
    matches = [
        record for record in records["Checkpoints"]
        if record.get("checkpoint_id") == authority["checkpoint_id"]
    ]
    if len(matches) != 1:
        error("state_corrupt", "topic dependency checkpoint authority is not retained")
    checkpoint = canonical_object(matches[0].get("data_json"), "checkpoint data_json", error)
    if (
        checkpoint.get("topic_id") != prerequisite_topic_id
        or checkpoint.get("checkpoint_id") != authority["checkpoint_id"]
        or checkpoint.get("record_revision") != authority["record_revision"]
        or checkpoint.get("published_identity") != authority["published_identity"]
        or checkpoint.get("decision_digest") != authority["decision_digest"]
    ):
        error("state_corrupt", "topic dependency checkpoint authority is incoherent")


def _validate_phase_result_provenance(
    records: dict[str, list[dict[str, Any]]], prerequisite_topic_id: str,
    authority: dict[str, Any], decisions: list[dict[str, str]],
    error: Callable[[str, str], None],
) -> None:
    _validate_retained_decisions(records, prerequisite_topic_id, decisions, error)
    matches = [
        record for record in records["Phase Results"]
        if record.get("result_id") == authority["result_id"]
    ]
    if len(matches) != 1:
        error("state_corrupt", "topic dependency Phase Result authority is not retained")
    result = canonical_object(matches[0].get("data_json"), "Phase Result data_json", error)
    full_authority = result.get("decision_authority")
    full_pairs = {
        entry["decision_id"]: entry["sha256"]
        for entry in full_authority
    } if _decision_pairs(full_authority) else None
    if (
        result.get("topic_id") != prerequisite_topic_id
        or result.get("result_id") != authority["result_id"]
        or result.get("phase_run_id") != authority["phase_run_id"]
        or result.get("affected_decision_ids") != authority["affected_decision_ids"]
        or full_pairs is None
        or any(full_pairs.get(entry["decision_id"]) != entry["sha256"] for entry in decisions)
        or matches[0].get("record_revision") != authority["record_revision"]
    ):
        error("state_corrupt", "topic dependency Phase Result authority is incoherent")


def _confirmed_authority_is_valid(
    authority: Any, decisions: list[dict[str, str]],
) -> bool:
    return (
        isinstance(authority, dict)
        and set(authority) == {"decision_set_digest"}
        and isinstance(authority["decision_set_digest"], str)
        and SHA256_RE.fullmatch(authority["decision_set_digest"])
        and bool(decisions)
        and authority["decision_set_digest"] == decision_pair_digest(decisions)
    )


def _confirmed_authority_for_selection(
    candidate: dict[str, Any], decisions: list[dict[str, str]],
) -> dict[str, Any]:
    return {"decision_set_digest": decision_pair_digest(decisions)}


def _candidate_authority_for_selection(
    candidate: dict[str, Any], decisions: list[dict[str, str]],
) -> dict[str, Any] | None:
    authority = candidate.get("authority")
    return authority if isinstance(authority, dict) else None


def _checkpoint_authority_is_valid(
    authority: Any, decisions: list[dict[str, str]],
) -> bool:
    return (
        isinstance(authority, dict)
        and set(authority) == {
            "checkpoint_id", "record_revision", "published_identity", "decision_digest",
        }
        and _identity(authority["checkpoint_id"], "CP")
        and type(authority["record_revision"]) is int
        and authority["record_revision"] >= 1
        and isinstance(authority["published_identity"], str)
        and isinstance(authority["decision_digest"], str)
        and SHA256_RE.fullmatch(authority["decision_digest"])
    )


def _phase_result_authority_is_valid(
    authority: Any, decisions: list[dict[str, str]],
) -> bool:
    return (
        isinstance(authority, dict)
        and set(authority) == {
            "result_id", "record_revision", "state", "phase_run_id", "affected_decision_ids",
        }
        and _identity(authority["result_id"], "PH")
        and type(authority["record_revision"]) is int
        and authority["record_revision"] >= 1
        and authority["state"] == "completed"
        and isinstance(authority["phase_run_id"], str)
        and canonical_string_array(authority["affected_decision_ids"])
    )


AUTHORITY_DESCRIPTORS.update({
    "confirmed-decision": AuthorityDescriptor(
        "confirmed-decision", "confirmed", None, True,
        _confirmed_authority_is_valid, _confirmed_authority_for_selection,
        _validate_confirmed_decision_provenance,
    ),
    "phase-0-checkpoint": AuthorityDescriptor(
        "phase-0-checkpoint", "checkpoint", "checkpoint_id", False,
        _checkpoint_authority_is_valid, _candidate_authority_for_selection,
        _validate_checkpoint_provenance,
    ),
    "phase-1-result": AuthorityDescriptor(
        "phase-1-result", "phase_result", "result_id", False,
        _phase_result_authority_is_valid, _candidate_authority_for_selection,
        _validate_phase_result_provenance,
    ),
})


def validate_dependency_records(
    records: dict[str, list[dict[str, Any]]], error: Callable[[str, str], None],
) -> None:
    """Validate ledger-only invariants without importing state or protocol owners."""
    error_factory = error

    def error(code: str, message: str) -> None:
        raise error_factory(code, message)

    topics = {item.get("topic_id") for item in records["Current Topics"]}
    seen_ids: set[str] = set()
    edges: dict[str, set[str]] = {}
    pairs: set[tuple[str, str]] = set()
    for item in records["Topic Dependencies"]:
        if set(item) != RECORD_FIELDS:
            error("state_corrupt", "topic dependency record fields are invalid")
        dep_id = item["dependency_id"]
        if not _identity(dep_id, "DEP") or dep_id in seen_ids:
            error("state_corrupt", "topic dependency identity is invalid or duplicated")
        seen_ids.add(dep_id)
        if type(item["record_revision"]) is not int or item["record_revision"] < 1:
            error("state_corrupt", "topic dependency revision is invalid")
        dependent, prerequisite = item["dependent_topic_id"], item["prerequisite_topic_id"]
        if dependent not in topics or prerequisite not in topics or dependent == prerequisite:
            error("state_corrupt", "topic dependency endpoints are invalid")
        if not isinstance(item["requirement_kind"], str) or item["requirement_kind"] not in DEPENDENCY_AUTHORITY_KINDS or not isinstance(item["requirement_summary"], str) or not item["requirement_summary"] or len(item["requirement_summary"].encode("utf-8")) > 4096:
            error("state_corrupt", "topic dependency requirement is invalid")
        if item["relation_state"] not in {"active", "cancelled"} or item["gate_state"] not in {"closed", "open"}:
            error("state_corrupt", "topic dependency state is invalid")
        _gate_reason(item["gate_reason_json"], error)
        if item["accepted_basis_json"] is not None:
            basis = canonical_object(item["accepted_basis_json"], "topic dependency accepted_basis_json", error)
            decisions = basis.get("decision_authority")
            historical_basis = (
                basis.get("prerequisite_topic_id") != prerequisite
                or basis.get("requirement_kind") != item["requirement_kind"]
            )
            dependent_phase = next(
                (topic.get("current_phase") for topic in records["Current Topics"]
                 if topic.get("topic_id") == item["dependent_topic_id"]),
                None,
            )
            if (
                item["relation_state"] == "active"
                and item["gate_state"] == "open"
                and dependent_phase in {0, 1}
                and historical_basis
            ):
                error("state_corrupt", "enforcing topic dependency basis must be current")
            if (
                basis.get("basis_version") != 1
                or basis.get("dependency_id") != dep_id
                or (not historical_basis and basis.get("prerequisite_topic_id") != prerequisite)
                or (not historical_basis and basis.get("requirement_kind") != item["requirement_kind"])
                or not _decision_pairs(decisions)
                or set(basis) != ({"basis_version", "dependency_id", "prerequisite_topic_id", "requirement_kind", "decision_authority", "authority"} | ({"child_result_id"} if "child_result_id" in basis else set()))
            ):
                error("state_corrupt", "topic dependency accepted basis is incoherent")
            authority = basis.get("authority")
            historical_replace = historical_basis
            authority_kind = basis["requirement_kind"] if historical_replace else item["requirement_kind"]
            descriptor = (
                authority_descriptor(authority_kind)
                if authority_kind in DEPENDENCY_AUTHORITY_KINDS
                else None
            )
            if descriptor is None or not descriptor.validate_authority(authority, decisions):
                error("state_corrupt", "topic dependency authority is incoherent")
            if historical_replace and (
                basis["prerequisite_topic_id"] not in topics
                or authority_kind not in DEPENDENCY_AUTHORITY_KINDS
            ):
                error("state_corrupt", "historical topic dependency basis endpoints are invalid")
            # A currently enforcing open gate must resolve current authority.
            # Closed/cancelled/Phase-2 records retain historical evidence even
            # after a legitimate invalidation changes the source decisions.
            if (
                item["relation_state"] == "active"
                and item["gate_state"] == "open"
                and dependent_phase in {0, 1}
            ):
                descriptor.validate_retained_provenance(
                    records, basis["prerequisite_topic_id"], authority, decisions, error
                )
            child_result_id = basis.get("child_result_id")
            if child_result_id is not None:
                historical_prerequisite = basis["prerequisite_topic_id"]
                historical_kind = basis["requirement_kind"]
                child = next((record for record in records["Phase Results"] if record.get("result_id") == child_result_id), None)
                if (
                    not _identity(child_result_id, "CR")
                    or child is None
                    or child.get("result_kind") != "child-topic-result"
                    or child.get("source_topic_id") != historical_prerequisite
                ):
                    error("state_corrupt", "topic dependency child result provenance is incoherent")
                frozen = canonical_object(child.get("authority_json"), "child result authority_json", error)
                frozen_pairs = frozen.get("decision_authority")
                frozen_ids = frozen.get("decision_ids")
                frozen_fields = {
                    "authority_kind", "authority_identity", "decision_ids",
                    "decision_authority", "topic_phase",
                } | ({"authority"} if "authority" in frozen else set())
                if (
                    set(frozen) != frozen_fields
                    or
                    frozen.get("authority_kind") != historical_kind
                    or not canonical_string_array(frozen_ids)
                    or not _decision_pairs(frozen_pairs)
                    or frozen_ids != [entry["decision_id"] for entry in decisions]
                    or frozen_pairs != decisions
                    or frozen.get("authority") != authority
                ):
                    error("state_corrupt", "topic dependency child basis does not match frozen authority")
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
