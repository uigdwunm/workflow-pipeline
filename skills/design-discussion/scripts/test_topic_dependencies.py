"""Public CLI Ticket-07 dependency matrix, separated from legacy evolution tests."""

from __future__ import annotations

import hashlib
import json
import importlib
import uuid
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).parent))

import discussion_protocol as PROTOCOL
from test_topic_dependency_support import TopicDependencyScenarioMixin
from discussion_core.topic_dependency_schema import authority_descriptor


class TopicDependencyCliTests(TopicDependencyScenarioMixin, unittest.TestCase):
    """Reuse the evolution fixture without inheriting its unrelated test methods."""

    def _evaluated_confirmed_dependency(self, name: str) -> tuple[dict[str, object], dict[str, object], Path, dict[str, object]]:
        protocol = importlib.import_module("discussion_protocol")
        project = self.make_project(name, git=False)
        topic = self.bootstrap_topic(project)
        child = self.prepare_child_handoff(topic, initial_dependencies=[{
            "dependent_endpoint": "source", "prerequisite_topic_ref": "target",
            "requirement_kind": "confirmed-decision", "requirement_summary": "Child decision.",
        }])
        ledger = Path(str(topic["ledger_path"]))
        frontmatter, records = protocol._load_records(ledger)
        decision = {"decision_id": "D-child", "summary": "Typed.", "rationale": "Current.", "state": "confirmed", "evolution": "confirmed"}
        records["Pending Items"].append({"item_id": "D-child", "item_kind": "decision", "topic_id": child["target_topic_id"], "data_json": protocol._canonical_json(decision)})
        ledger.write_bytes(protocol._render_records_ledger(frontmatter, records))
        dependency_id = child["initial_dependencies"][0]["dependency_id"]
        code, evaluation, stderr = self.run_cli(self.evolution_request(topic, operation="evaluate-topic-gate", basis_selection=[{"dependency_id": dependency_id, "decision_ids": ["D-child"]}]))
        self.assertEqual(code, 0, stderr)
        return topic, child, ledger, evaluation

    def _assert_stale_release(self, topic: dict[str, object], ledger: Path, evaluation: dict[str, object], *, expected_revision: int) -> None:
        before = ledger.read_bytes()
        code, rejected, _ = self.run_cli(self.evolution_request(topic, operation="release-topic-gate", expected_revision=expected_revision, expected_topic_revision=1, release_set=evaluation["release_set"], release_set_sha256=evaluation["release_set_sha256"]))
        self.assertEqual(code, 1)
        self.assertEqual(rejected["error"]["code"], "topic_gate_evaluation_stale")
        self.assertEqual(rejected["error"]["context"]["ledger_revision"], expected_revision)
        self.assertEqual(rejected["error"]["context"]["topic_revision"], 1)
        self.assertEqual(ledger.read_bytes(), before)

    def test_ticket07_authority_descriptors_own_basis_validation_and_selection(self) -> None:
        """Every authority kind converts and validates its own persisted shape."""
        decisions = [{"decision_id": "D-current", "sha256": "a" * 64}]
        cases = {
            "confirmed-decision": (
                {
                    "decision_set_digest": hashlib.sha256(
                        PROTOCOL._canonical_json(decisions).encode("utf-8")
                    ).hexdigest(),
                }, None,
            ),
            "phase-0-checkpoint": (
                {
                    "checkpoint_id": "CP-" + "c" * 32,
                    "record_revision": 1,
                    "published_identity": "checkpoint-artifact",
                    "decision_digest": "d" * 64,
                },
                "CP-" + "c" * 32,
            ),
            "phase-1-result": (
                {
                    "result_id": "PH-00000001",
                    "record_revision": 1,
                    "state": "completed",
                    "phase_run_id": "PR-00000001",
                    "affected_decision_ids": ["D-current"],
                },
                "PH-00000001",
            ),
        }
        for kind, (authority, expected_identity) in cases.items():
            with self.subTest(kind=kind):
                descriptor = authority_descriptor(kind)
                self.assertTrue(descriptor.validate_authority(authority, decisions))
                self.assertFalse(descriptor.validate_authority({**authority, "extra": True}, decisions))
                candidate = {"authority": authority}
                basis = descriptor.basis_from_candidate(
                    candidate,
                    dependency_id="DEP-" + "e" * 32,
                    prerequisite_topic_id="topic-prerequisite",
                    decision_authority=decisions,
                )
                selection = descriptor.selection_from_basis(basis)
                self.assertEqual(selection["decision_ids"], ["D-current"])
                if expected_identity is None:
                    self.assertNotIn("authority_id", selection)
                else:
                    self.assertEqual(selection["authority_id"], expected_identity)

    def test_ticket07_dependency_create_replay_is_cli_idempotent(self) -> None:
        project = self.make_project("ticket07-dependency-replay", git=False)
        topic = self.bootstrap_topic(project)
        child = self.prepare_child_handoff(topic)
        request = self.evolution_request(
            topic, operation="update-topic-dependency", expected_revision=2,
            expected_topic_revision=1, action="create",
            prerequisite_topic_id=child["target_topic_id"],
            requirement_kind="confirmed-decision", requirement_summary="The API is chosen.",
        )
        code, first, stderr = self.run_cli(request)
        self.assertEqual(code, 0, stderr)
        ledger = Path(str(topic["ledger_path"]))
        committed = ledger.read_bytes()
        code, replay, stderr = self.run_cli(request)
        self.assertEqual(code, 0, stderr)
        self.assertTrue(replay["idempotent_replay"])
        self.assertEqual(replay["dependency_id"], first["dependency_id"])
        self.assertEqual(ledger.read_bytes(), committed)

    def test_ticket07_release_rejects_dependency_record_drift_via_cli(self) -> None:
        topic, child, ledger, evaluation = self._evaluated_confirmed_dependency("ticket07-release-dependency-drift")
        dependency_id = evaluation["release_set"][0]["dependency_id"]
        code, _, stderr = self.run_cli(self.evolution_request(topic, operation="update-topic-dependency", expected_revision=2, expected_topic_revision=1, action="replace", dependency_id=dependency_id, expected_dependency_revision=1, prerequisite_topic_id=child["target_topic_id"], requirement_kind="phase-1-result", requirement_summary="New edge."))
        self.assertEqual(code, 0, stderr)
        self._assert_stale_release(topic, ledger, evaluation, expected_revision=3)

    def test_ticket07_release_rejects_confirmed_decision_digest_drift_via_cli(self) -> None:
        protocol = importlib.import_module("discussion_protocol")
        topic, _, ledger, evaluation = self._evaluated_confirmed_dependency("ticket07-release-decision-drift")
        frontmatter, records = protocol._load_records(ledger)
        item = next(record for record in records["Pending Items"] if record["item_id"] == "D-child")
        decision = json.loads(str(item["data_json"]))
        decision["summary"] = "Typed requests changed."
        item["data_json"] = protocol._canonical_json(decision)
        ledger.write_bytes(protocol._render_records_ledger(frontmatter, records))
        self._assert_stale_release(topic, ledger, evaluation, expected_revision=2)

    def test_ticket07_confirmed_gate_release_persists_only_selected_decisions_via_cli(self) -> None:
        protocol = importlib.import_module("discussion_protocol")
        project = self.make_project("ticket07-confirmed-narrow-release", git=False)
        topic = self.bootstrap_topic(project)
        child = self.prepare_child_handoff(topic, initial_dependencies=[{
            "dependent_endpoint": "source", "prerequisite_topic_ref": "target",
            "requirement_kind": "confirmed-decision", "requirement_summary": "One child decision is enough.",
        }])
        ledger = Path(str(topic["ledger_path"]))
        frontmatter, records = protocol._load_records(ledger)
        for decision_id in ("D-first", "D-second"):
            decision = {
                "decision_id": decision_id, "summary": decision_id, "rationale": "Current.",
                "state": "confirmed", "evolution": "confirmed",
            }
            records["Pending Items"].append({
                "item_id": decision_id, "item_kind": "decision",
                "topic_id": child["target_topic_id"], "data_json": protocol._canonical_json(decision),
            })
        ledger.write_bytes(protocol._render_records_ledger(frontmatter, records))
        dependency_id = child["initial_dependencies"][0]["dependency_id"]
        code, evaluation, stderr = self.run_cli(self.evolution_request(
            topic, operation="evaluate-topic-gate", basis_selection=[{
                "dependency_id": dependency_id, "decision_ids": ["D-first"],
            }],
        ))
        self.assertEqual(code, 0, stderr)
        code, released, stderr = self.run_cli(self.evolution_request(
            topic, operation="release-topic-gate", expected_revision=2,
            expected_topic_revision=1, release_set=evaluation["release_set"],
            release_set_sha256=evaluation["release_set_sha256"],
        ))
        self.assertEqual(code, 0, stderr)
        basis = released["accepted_bases"][0]["basis"]
        pairs = [{"decision_id": "D-first", "sha256": basis["decision_authority"][0]["sha256"]}]
        self.assertEqual(basis["decision_authority"], pairs)
        self.assertEqual(
            basis["authority"]["decision_set_digest"],
            hashlib.sha256(protocol._canonical_json(pairs).encode("utf-8")).hexdigest(),
        )

    def test_ticket07_tampered_confirmed_basis_digest_fails_closed_via_cli(self) -> None:
        for historical in (False, True):
            with self.subTest(historical=historical):
                topic, child, ledger, evaluation = self._evaluated_confirmed_dependency(
                    f"ticket07-tampered-basis-{historical}"
                )
                code, _, stderr = self.run_cli(self.evolution_request(
                    topic, operation="release-topic-gate", expected_revision=2,
                    expected_topic_revision=1, release_set=evaluation["release_set"],
                    release_set_sha256=evaluation["release_set_sha256"],
                ))
                self.assertEqual(code, 0, stderr)
                dependency_id = evaluation["release_set"][0]["dependency_id"]
                if historical:
                    code, _, stderr = self.run_cli(self.evolution_request(
                        topic, operation="update-topic-dependency", expected_revision=3,
                        expected_topic_revision=1, action="replace",
                        dependency_id=dependency_id, expected_dependency_revision=2,
                        prerequisite_topic_id=child["target_topic_id"],
                        requirement_kind="phase-1-result", requirement_summary="Historical basis.",
                    ))
                    self.assertEqual(code, 0, stderr)
                frontmatter, records = PROTOCOL._load_records(ledger)
                dependency = next(
                    item for item in records["Topic Dependencies"]
                    if item["dependency_id"] == dependency_id
                )
                basis = json.loads(str(dependency["accepted_basis_json"]))
                basis["authority"]["decision_set_digest"] = "f" * 64
                dependency["accepted_basis_json"] = PROTOCOL._canonical_json(basis)
                ledger.write_bytes(PROTOCOL._render_records_ledger(frontmatter, records))
                before = ledger.read_bytes()
                code, rejected, _ = self.run_cli(
                    self.evolution_request(topic, operation="read-topic")
                )
                self.assertEqual(code, 1)
                self.assertEqual(rejected["error"]["code"], "state_corrupt")
                self.assertEqual(ledger.read_bytes(), before)

    def test_ticket07_reclosed_drifted_basis_can_be_replaced_but_not_tampered_via_cli(self) -> None:
        topic, child, ledger, evaluation = self._evaluated_confirmed_dependency(
            "ticket07-historical-basis-after-drift"
        )
        dependency_id = evaluation["release_set"][0]["dependency_id"]
        code, _, stderr = self.run_cli(self.evolution_request(
            topic, operation="release-topic-gate", expected_revision=2,
            expected_topic_revision=1, release_set=evaluation["release_set"],
            release_set_sha256=evaluation["release_set_sha256"],
        ))
        self.assertEqual(code, 0, stderr)
        frontmatter, records = PROTOCOL._load_records(ledger)
        dependency = next(item for item in records["Topic Dependencies"] if item["dependency_id"] == dependency_id)
        dependency["gate_state"] = "closed"
        dependency["record_revision"] = 3
        dependency["gate_reason_json"] = PROTOCOL._canonical_json({
            "kind": "direct-upstream-invalidation", "ledger_revision": 3,
            "topic_update_id": "DW-" + "a" * 32, "decision_id": "D-child", "action": "replace",
        })
        decision_record = next(item for item in records["Pending Items"] if item["item_id"] == "D-child")
        decision = json.loads(str(decision_record["data_json"]))
        decision["summary"] = "The upstream authority changed after reclosure."
        decision_record["data_json"] = PROTOCOL._canonical_json(decision)
        ledger.write_bytes(PROTOCOL._render_records_ledger(frontmatter, records))
        code, replaced, stderr = self.run_cli(self.evolution_request(
            topic, operation="update-topic-dependency", expected_revision=3,
            expected_topic_revision=1, action="replace", dependency_id=dependency_id,
            expected_dependency_revision=3, prerequisite_topic_id=child["target_topic_id"],
            requirement_kind="phase-1-result", requirement_summary="Audit the prior basis only.",
        ))
        self.assertEqual(code, 0, stderr)
        self.assertEqual(replaced["state"], "replace")
        code, reread, stderr = self.run_cli(
            self.evolution_request(topic, operation="read-topic")
        )
        self.assertEqual(code, 0, stderr)
        historical = next(item for item in reread["topic_dependencies"] if item["dependency_id"] == dependency_id)
        basis = json.loads(str(historical["accepted_basis_json"]))
        self.assertEqual(basis["prerequisite_topic_id"], child["target_topic_id"])
        frontmatter, records = PROTOCOL._load_records(ledger)
        dependency = next(item for item in records["Topic Dependencies"] if item["dependency_id"] == dependency_id)
        basis = json.loads(str(dependency["accepted_basis_json"]))
        basis["authority"]["decision_set_digest"] = "f" * 64
        dependency["accepted_basis_json"] = PROTOCOL._canonical_json(basis)
        ledger.write_bytes(PROTOCOL._render_records_ledger(frontmatter, records))
        before = ledger.read_bytes()
        code, rejected, _ = self.run_cli(
            self.evolution_request(topic, operation="read-topic")
        )
        self.assertEqual(code, 1)
        self.assertEqual(rejected["error"]["code"], "state_corrupt")
        self.assertEqual(ledger.read_bytes(), before)

    def test_ticket07_phase2_dependency_history_is_immutable_via_cli(self) -> None:
        project = self.make_project("ticket07-phase2-cutoff", git=False)
        topic = self.bootstrap_topic(project)
        child = self.prepare_child_handoff(topic)
        code, _, stderr = self.run_cli(self.evolution_request(
            topic, operation="update-topic-dependency", expected_revision=2,
            expected_topic_revision=1, action="create", prerequisite_topic_id=child["target_topic_id"],
            requirement_kind="confirmed-decision", requirement_summary="The child decides the API."))
        self.assertEqual(code, 0, stderr)
        ledger = Path(str(topic["ledger_path"]))
        self.rewrite_ledger_with_valid_digest(ledger, "current_phase: 0", "current_phase: 2")
        before = ledger.read_bytes()
        code, rejected, _ = self.run_cli(self.evolution_request(
            topic, operation="release-topic-gate", expected_revision=3,
            expected_topic_revision=1, release_set=[], release_set_sha256="0" * 64))
        self.assertEqual(code, 1)
        self.assertEqual(rejected["error"]["code"], "topic_dependency_phase_conflict")
        self.assertEqual(rejected["error"]["context"], {
            "dependent_topic_id": topic["topic_id"], "phase": 2,
        })
        self.assertEqual(ledger.read_bytes(), before)

    def test_ticket07_direct_invalidation_does_not_propagate_via_cli(self) -> None:
        """An upstream update recloses only the directly dependent gate."""
        protocol = importlib.import_module("discussion_protocol")
        project = self.make_project("ticket07-direct-invalidation", git=False)
        topic = self.bootstrap_topic(project)
        ledger = Path(str(topic["ledger_path"]))
        a_id = str(topic["topic_id"])
        b_id = "topic-" + "b" * 32
        c_id = "topic-" + "c" * 32
        a_decision = {"decision_id": "D-a", "summary": "Keep the initial API.", "rationale": "It is the current authority.", "state": "confirmed", "evolution": "confirmed"}
        b_decision = {"decision_id": "D-b", "summary": "Build on A.", "rationale": "It is the current authority.", "state": "confirmed", "evolution": "confirmed"}
        frontmatter, records = protocol._load_records(ledger)
        records["Current Topics"].extend([
            {"topic_id": b_id, "record_revision": 1, "root_slug": "topic-b", "parent_topic_id": a_id, "current_phase": 1, "phase_state": "active", "review_state": "unreviewed", "topic_state": "open", "topic_document_path": None},
            {"topic_id": c_id, "record_revision": 1, "root_slug": "topic-c", "parent_topic_id": b_id, "current_phase": 1, "phase_state": "active", "review_state": "unreviewed", "topic_state": "open", "topic_document_path": None},
        ])
        records["Conversation Bindings"].extend([
            {"topic_id": b_id, "conversation_ref": "codex-thread:b", "binding_state": "active", "record_revision": 1, "handoff_id": None, "attempt_id": None},
            {"topic_id": c_id, "conversation_ref": "codex-thread:c", "binding_state": "active", "record_revision": 1, "handoff_id": None, "attempt_id": None},
        ])
        records["Pending Items"].extend([
            {"item_id": "D-a", "item_kind": "decision", "topic_id": a_id, "data_json": protocol._canonical_json(a_decision)},
            {"item_id": "D-b", "item_kind": "decision", "topic_id": b_id, "data_json": protocol._canonical_json(b_decision)},
        ])
        b_dependency_id = "DEP-" + "1" * 32
        c_dependency_id = "DEP-" + "2" * 32
        fixture_reason = protocol._canonical_json({"kind": "explicit-create", "dependency_update_id": "00000000-0000-4000-8000-000000000000", "ledger_revision": 1})
        records["Topic Dependencies"].extend([
            {"dependency_id": b_dependency_id, "record_revision": 1, "dependent_topic_id": b_id, "prerequisite_topic_id": a_id, "requirement_kind": "confirmed-decision", "requirement_summary": "A remains current.", "relation_state": "active", "gate_state": "closed", "accepted_basis_json": None, "gate_reason_json": fixture_reason},
            {"dependency_id": c_dependency_id, "record_revision": 1, "dependent_topic_id": c_id, "prerequisite_topic_id": b_id, "requirement_kind": "confirmed-decision", "requirement_summary": "B remains current.", "relation_state": "active", "gate_state": "closed", "accepted_basis_json": None, "gate_reason_json": fixture_reason},
        ])
        ledger.write_bytes(protocol._render_records_ledger(frontmatter, records))

        def gate_request(actor: str, owner: str, operation: str, **values: object) -> dict[str, object]:
            request = self.evolution_request(topic, operation=operation, **values)
            request["actor_topic_id"] = actor
            request["actor_conversation_ref"] = owner
            return request

        for actor, owner, dependency_id, decision_id, revision in (
            (b_id, "codex-thread:b", b_dependency_id, "D-a", 1),
            (c_id, "codex-thread:c", c_dependency_id, "D-b", 2),
        ):
            code, evaluated, stderr = self.run_cli(gate_request(actor, owner, "evaluate-topic-gate", basis_selection=[{"dependency_id": dependency_id, "decision_ids": [decision_id]}]))
            self.assertEqual(code, 0, stderr)
            code, released, stderr = self.run_cli(gate_request(actor, owner, "release-topic-gate", expected_revision=revision, expected_topic_revision=1, release_set=evaluated["release_set"], release_set_sha256=evaluated["release_set_sha256"]))
            self.assertEqual(code, 0, stderr)
            self.assertEqual(released["state"], "open")

        _, before_records = protocol._load_records(ledger)
        before_c = next(item for item in before_records["Topic Dependencies"] if item["dependency_id"] == c_dependency_id)
        changed, ledger_revision, topic_revision = self.complete_update(project, topic, ledger_revision=3, topic_revision=1, mutation={"type": "change-direction", "summary": "Replace A.", "affected_decision_ids": ["D-a"]})
        update_request = self.evolution_request(topic, operation="prepare-topic-update", expected_revision=ledger_revision, expected_topic_revision=topic_revision, mutation={"type": "resolve-impact", "impact_id": changed["impact_ids"][0], "decision_id": "D-a", "action": "replace", "summary": "A was replaced."})
        code, update, stderr = self.run_cli(update_request)
        self.assertEqual(code, 0, stderr)
        _, after_records = protocol._load_records(ledger)
        b_dependency = next(item for item in after_records["Topic Dependencies"] if item["dependency_id"] == b_dependency_id)
        c_dependency = next(item for item in after_records["Topic Dependencies"] if item["dependency_id"] == c_dependency_id)
        self.assertEqual(b_dependency["gate_state"], "closed")
        self.assertEqual(b_dependency["record_revision"], 3)
        reason = json.loads(str(b_dependency["gate_reason_json"]))
        self.assertEqual(reason["topic_update_id"], f"DW-{uuid.UUID(str(update_request['idempotency_key'])).hex}")
        self.assertEqual(c_dependency, before_c)
        self.assertEqual(next(item for item in after_records["Current Topics"] if item["topic_id"] == c_id)["phase_state"], "active")

    def test_ticket07_upstream_change_skips_phase2_dependents_via_cli(self) -> None:
        protocol = importlib.import_module("discussion_protocol")
        project = self.make_project("ticket07-phase2-invalidation", git=False)
        topic = self.bootstrap_topic(project)
        ledger = Path(str(topic["ledger_path"]))
        a_id = str(topic["topic_id"])
        b_id = "topic-" + "b" * 32
        decision = {"decision_id": "D-a", "summary": "Keep A.", "rationale": "Current authority.", "state": "confirmed", "evolution": "confirmed"}
        digest = hashlib.sha256(protocol._canonical_json(decision).encode("utf-8")).hexdigest()
        authority = [{"decision_id": "D-a", "sha256": digest}]
        dependency_id = "DEP-" + "3" * 32
        basis = {"basis_version": 1, "dependency_id": dependency_id, "prerequisite_topic_id": a_id, "requirement_kind": "confirmed-decision", "decision_authority": [{"decision_id": "D-a", "sha256": digest}], "authority": {"decision_set_digest": hashlib.sha256(protocol._canonical_json(authority).encode("utf-8")).hexdigest()}}
        frontmatter, records = protocol._load_records(ledger)
        records["Current Topics"].append({"topic_id": b_id, "record_revision": 1, "root_slug": "topic-b", "parent_topic_id": a_id, "current_phase": 2, "phase_state": "active", "review_state": "unreviewed", "topic_state": "open", "topic_document_path": None})
        records["Pending Items"].append({"item_id": "D-a", "item_kind": "decision", "topic_id": a_id, "data_json": protocol._canonical_json(decision)})
        records["Topic Dependencies"].append({"dependency_id": dependency_id, "record_revision": 2, "dependent_topic_id": b_id, "prerequisite_topic_id": a_id, "requirement_kind": "confirmed-decision", "requirement_summary": "A remains current.", "relation_state": "active", "gate_state": "open", "accepted_basis_json": protocol._canonical_json(basis), "gate_reason_json": protocol._canonical_json({"kind": "atomic-release", "release_id": "00000000-0000-4000-8000-000000000000", "ledger_revision": 1})})
        ledger.write_bytes(protocol._render_records_ledger(frontmatter, records))
        _, before_records = protocol._load_records(ledger)
        before_dependency = next(item for item in before_records["Topic Dependencies"] if item["dependency_id"] == dependency_id)
        changed, ledger_revision, topic_revision = self.complete_update(project, topic, ledger_revision=1, topic_revision=1, mutation={"type": "change-direction", "summary": "Replace A.", "affected_decision_ids": ["D-a"]})
        code, _, stderr = self.run_cli(self.evolution_request(topic, operation="prepare-topic-update", expected_revision=ledger_revision, expected_topic_revision=topic_revision, mutation={"type": "resolve-impact", "impact_id": changed["impact_ids"][0], "decision_id": "D-a", "action": "replace", "summary": "A was replaced."}))
        self.assertEqual(code, 0, stderr)
        _, after_records = protocol._load_records(ledger)
        self.assertEqual(next(item for item in after_records["Topic Dependencies"] if item["dependency_id"] == dependency_id), before_dependency)

    def test_ticket07_child_result_requires_selection_when_current_authority_exists(self) -> None:
        project = self.make_project("ticket07-child-result-authority", git=False)
        topic = self.bootstrap_topic(project)
        prepared, child_ref = self.activate_child_handoff(topic)
        self.complete_update(project, topic, ledger_revision=5, topic_revision=1, mutation={"type": "confirm-decision", "summary": "Use typed requests.", "rationale": "The child has selected its public authority."})
        ledger = Path(str(topic["ledger_path"]))
        pending_marker = "## Pending Items\n\n"
        current = ledger.read_text(encoding="utf-8")
        pending_start = current.index(pending_marker)
        pending_end = current.index("\n## ", pending_start + len(pending_marker))
        pending = current[pending_start:pending_end]
        self.rewrite_ledger_with_valid_digest(ledger, pending, pending.replace(str(topic["topic_id"]), str(prepared["target_topic_id"])))
        before = ledger.read_bytes()
        request = self.handoff_request(topic, operation="submit-child-result", ledger_revision=7, topic_revision=1, owner_ref=child_ref, handoff_id=prepared["handoff_id"], attempt_id=prepared["attempt_id"], result_scope=["api"], summary="Use typed requests.")
        request["actor_topic_id"] = prepared["target_topic_id"]
        code, rejected, _ = self.run_cli(request)
        self.assertEqual(code, 1)
        self.assertEqual(rejected["error"]["code"], "topic_dependency_authority_selection_required")
        self.assertEqual(ledger.read_bytes(), before)

    def test_ticket07_dependency_request_type_and_summary_bounds_via_cli(self) -> None:
        project = self.make_project("ticket07-dependency-request-bounds", git=False)
        topic = self.bootstrap_topic(project)
        child = self.prepare_child_handoff(topic)
        ledger = Path(str(topic["ledger_path"]))
        before = ledger.read_bytes()
        common = {
            "expected_revision": 2,
            "expected_topic_revision": 1,
            "action": "create",
            "prerequisite_topic_id": child["target_topic_id"],
            "requirement_kind": "confirmed-decision",
            "requirement_summary": "The child provides the authority.",
        }
        for field, value in (
            ("action", None), ("action", []), ("action", {}),
            ("prerequisite_topic_id", []), ("prerequisite_topic_id", {}),
            ("requirement_kind", None), ("requirement_kind", []),
            ("requirement_summary", "x" * 4097),
        ):
            with self.subTest(field=field, value_type=type(value).__name__):
                request = self.evolution_request(
                    topic, operation="update-topic-dependency", **{**common, field: value}
                )
                code, rejected, _ = self.run_cli(request)
                self.assertEqual(code, 1)
                self.assertEqual(rejected["error"]["code"], "invalid_request")
                self.assertEqual(ledger.read_bytes(), before)

    def test_ticket07_dependency_error_context_matrix_via_cli(self) -> None:
        """Dependency failures name the affected edge and never partially write."""
        protocol = importlib.import_module("discussion_protocol")
        project = self.make_project("ticket07-dependency-error-context", git=False)
        topic = self.bootstrap_topic(project)
        ledger = Path(str(topic["ledger_path"]))
        dependent_id = str(topic["topic_id"])
        prerequisite_id = "topic-" + "d" * 32
        frontmatter, records = protocol._load_records(ledger)
        records["Current Topics"].append({
            "topic_id": prerequisite_id, "record_revision": 1,
            "root_slug": "prerequisite", "parent_topic_id": dependent_id,
            "current_phase": 1, "phase_state": "active", "review_state": "unreviewed",
            "topic_state": "open", "topic_document_path": None,
        })
        records["Conversation Bindings"].append({
            "topic_id": prerequisite_id, "conversation_ref": "codex-thread:prerequisite",
            "binding_state": "active", "record_revision": 1,
            "handoff_id": None, "attempt_id": None,
        })
        ledger.write_bytes(protocol._render_records_ledger(frontmatter, records))
        create = self.evolution_request(
            topic, operation="update-topic-dependency", expected_revision=1,
            expected_topic_revision=1, action="create",
            prerequisite_topic_id=prerequisite_id,
            requirement_kind="confirmed-decision", requirement_summary="Await the prerequisite decision.",
        )
        code, created, stderr = self.run_cli(create)
        self.assertEqual(code, 0, stderr)
        dependency_id = created["dependency_id"]
        committed = ledger.read_bytes()

        def reject(request: dict[str, object], code_name: str, context: dict[str, object]) -> None:
            with self.subTest(code=code_name):
                code, response, _ = self.run_cli(request)
                self.assertEqual(code, 1)
                self.assertEqual(response["error"]["code"], code_name)
                self.assertEqual(response["error"]["context"], context)
                self.assertEqual(ledger.read_bytes(), committed)

        reject(self.evolution_request(
            topic, operation="update-topic-dependency", expected_revision=2,
            expected_topic_revision=1, action="cancel", dependency_id=dependency_id,
            expected_dependency_revision=99,
        ), "record_revision_conflict", {
            "dependency_id": dependency_id, "dependent_topic_id": dependent_id,
            "prerequisite_topic_id": prerequisite_id,
            "expected_dependency_revision": 99, "actual_dependency_revision": 1,
        })
        reject(self.evolution_request(
            topic, operation="update-topic-dependency", expected_revision=2,
            expected_topic_revision=1, action="create",
            prerequisite_topic_id=prerequisite_id, requirement_kind="confirmed-decision",
            requirement_summary="The duplicate edge.",
        ), "topic_dependency_duplicate", {
            "dependent_topic_id": dependent_id, "prerequisite_topic_id": prerequisite_id,
        })
        wrong_owner = self.evolution_request(
            topic, operation="update-topic-dependency", expected_revision=2,
            expected_topic_revision=1, action="cancel", dependency_id=dependency_id,
            expected_dependency_revision=1,
        )
        wrong_owner.update({"actor_topic_id": prerequisite_id, "actor_conversation_ref": "codex-thread:prerequisite"})
        reject(wrong_owner, "topic_dependency_ownership_conflict", {
            "dependency_id": dependency_id, "dependent_topic_id": dependent_id,
            "prerequisite_topic_id": prerequisite_id,
        })
        cycle = self.evolution_request(
            topic, operation="update-topic-dependency", expected_revision=2,
            expected_topic_revision=1, action="create",
            prerequisite_topic_id=dependent_id, requirement_kind="confirmed-decision",
            requirement_summary="This would make a cycle.",
        )
        cycle.update({"actor_topic_id": prerequisite_id, "actor_conversation_ref": "codex-thread:prerequisite"})
        reject(cycle, "topic_dependency_cycle", {
            "dependent_topic_id": prerequisite_id, "prerequisite_topic_id": dependent_id,
        })
        evidence = self.evolution_request(
            topic, operation="evaluate-topic-gate",
            basis_selection=[{"dependency_id": dependency_id, "decision_ids": []}],
        )
        reject(evidence, "topic_dependency_evidence_unavailable", {
            "dependency_id": dependency_id, "dependent_topic_id": dependent_id,
            "prerequisite_topic_id": prerequisite_id,
        })

    def test_ticket07_persisted_dependency_shape_is_state_corrupt_via_cli(self) -> None:
        """Malformed persisted nested data is fail-closed, not an internal error."""
        protocol = importlib.import_module("discussion_protocol")
        project = self.make_project("ticket07-dependency-persisted-shape", git=False)
        topic = self.bootstrap_topic(project)
        child = self.prepare_child_handoff(topic)
        ledger = Path(str(topic["ledger_path"]))
        code, _, stderr = self.run_cli(self.evolution_request(
            topic, operation="update-topic-dependency", expected_revision=2,
            expected_topic_revision=1, action="create",
            prerequisite_topic_id=child["target_topic_id"],
            requirement_kind="confirmed-decision", requirement_summary="The child supplies authority.",
        ))
        self.assertEqual(code, 0, stderr)
        frontmatter, records = protocol._load_records(ledger)
        dependency = records["Topic Dependencies"][0]
        dependency["gate_reason_json"] = protocol._canonical_json({
            "kind": "explicit-create", "dependency_update_id": [],
            "ledger_revision": 3, "unexpected": {"nested": True},
        })
        ledger.write_bytes(protocol._render_records_ledger(frontmatter, records))
        malformed = ledger.read_bytes()
        code, response, _ = self.run_cli(
            self.evolution_request(topic, operation="read-topic")
        )
        self.assertEqual(code, 1)
        self.assertEqual(response["error"]["code"], "state_corrupt")
        self.assertNotEqual(response["error"]["code"], "internal_error")
        self.assertEqual(ledger.read_bytes(), malformed)

    def test_ticket07_oversized_persisted_dependency_summary_is_state_corrupt_via_cli(self) -> None:
        """A ledger-only summary bound is enforced before any read response."""
        protocol = importlib.import_module("discussion_protocol")
        project = self.make_project("ticket07-oversized-persisted-summary", git=False)
        topic = self.bootstrap_topic(project)
        child = self.prepare_child_handoff(topic)
        ledger = Path(str(topic["ledger_path"]))
        code, _, stderr = self.run_cli(self.evolution_request(
            topic, operation="update-topic-dependency", expected_revision=2,
            expected_topic_revision=1, action="create",
            prerequisite_topic_id=child["target_topic_id"],
            requirement_kind="confirmed-decision", requirement_summary="Bounded summary.",
        ))
        self.assertEqual(code, 0, stderr)
        frontmatter, records = protocol._load_records(ledger)
        records["Topic Dependencies"][0]["requirement_summary"] = "x" * 4097
        ledger.write_bytes(protocol._render_records_ledger(frontmatter, records))
        malformed = ledger.read_bytes()
        code, response, _ = self.run_cli(
            self.evolution_request(topic, operation="read-topic")
        )
        self.assertEqual(code, 1)
        self.assertEqual(response["error"]["code"], "state_corrupt")
        self.assertEqual(ledger.read_bytes(), malformed)

    def test_ticket07_released_dependency_replace_keeps_historical_basis_via_cli(self) -> None:
        protocol = importlib.import_module("discussion_protocol")
        project = self.make_project("ticket07-replace-released", git=False)
        topic = self.bootstrap_topic(project)
        child = self.prepare_child_handoff(topic, initial_dependencies=[{
            "dependent_endpoint": "source", "prerequisite_topic_ref": "target",
            "requirement_kind": "confirmed-decision", "requirement_summary": "Child decision.",
        }])
        ledger = Path(str(topic["ledger_path"]))
        frontmatter, records = protocol._load_records(ledger)
        decision = {"decision_id": "D-child", "summary": "Typed.", "rationale": "Current.", "state": "confirmed", "evolution": "confirmed"}
        records["Pending Items"].append({"item_id": "D-child", "item_kind": "decision", "topic_id": child["target_topic_id"], "data_json": protocol._canonical_json(decision)})
        ledger.write_bytes(protocol._render_records_ledger(frontmatter, records))
        dependency_id = child["initial_dependencies"][0]["dependency_id"]
        code, evaluation, stderr = self.run_cli(self.evolution_request(topic, operation="evaluate-topic-gate", basis_selection=[{"dependency_id": dependency_id, "decision_ids": ["D-child"]}]))
        self.assertEqual(code, 0, stderr)
        code, _, stderr = self.run_cli(self.evolution_request(topic, operation="release-topic-gate", expected_revision=2, expected_topic_revision=1, release_set=evaluation["release_set"], release_set_sha256=evaluation["release_set_sha256"]))
        self.assertEqual(code, 0, stderr)
        _, before = protocol._load_records(ledger)
        historical_basis = next(item for item in before["Topic Dependencies"] if item["dependency_id"] == dependency_id)["accepted_basis_json"]
        code, replaced, stderr = self.run_cli(self.evolution_request(topic, operation="update-topic-dependency", expected_revision=3, expected_topic_revision=1, action="replace", dependency_id=dependency_id, expected_dependency_revision=2, prerequisite_topic_id=child["target_topic_id"], requirement_kind="phase-1-result", requirement_summary="Child Phase 1 result."))
        self.assertEqual(code, 0, stderr)
        self.assertEqual(replaced["state"], "replace")
        code, read, stderr = self.run_cli(self.evolution_request(topic, operation="read-topic"))
        self.assertEqual(code, 0, stderr)
        dependency = next(item for item in read["topic_dependencies"] if item["dependency_id"] == dependency_id)
        self.assertEqual(dependency["gate_state"], "closed")
        self.assertEqual(dependency["accepted_basis_json"], historical_basis)
        code, cancelled, stderr = self.run_cli(self.evolution_request(
            topic, operation="update-topic-dependency", expected_revision=4,
            expected_topic_revision=1, action="cancel", dependency_id=dependency_id,
            expected_dependency_revision=3,
        ))
        self.assertEqual(code, 0, stderr)
        self.assertEqual(cancelled["state"], "cancel")
        code, read, stderr = self.run_cli(self.evolution_request(
            topic, operation="read-topic"))
        self.assertEqual(code, 0, stderr)
        dependency = next(item for item in read["topic_dependencies"] if item["dependency_id"] == dependency_id)
        self.assertEqual(dependency["relation_state"], "cancelled")
        self.assertEqual(dependency["accepted_basis_json"], historical_basis)

    def test_ticket07_phase2_absorb_without_releases_skips_authority_checks_via_cli(self) -> None:
        project = self.make_project("ticket07-phase2-empty-absorb", git=False)
        topic = self.bootstrap_topic(project)
        prepared, child_ref = self.activate_child_handoff(topic)
        submit = self.handoff_request(
            topic, operation="submit-child-result", ledger_revision=5,
            owner_ref=child_ref, handoff_id=prepared["handoff_id"],
            attempt_id=prepared["attempt_id"], result_scope=["api"], summary="No release needed.",
        )
        submit["actor_topic_id"] = prepared["target_topic_id"]
        code, claimed, stderr = self.run_cli(submit)
        self.assertEqual(code, 0, stderr)
        ledger = Path(str(topic["ledger_path"]))
        self.rewrite_ledger_with_valid_digest(ledger, "current_phase: 0", "current_phase: 2")
        absorb = self.handoff_request(
            topic, operation="record-child-result", ledger_revision=6,
            handoff_id=prepared["handoff_id"], child_result_id=claimed["child_result_id"],
            effect="absorb",
        )
        code, absorbed, stderr = self.run_cli(absorb)
        self.assertEqual(code, 0, stderr)
        self.assertEqual(absorbed["state"], "absorbed")
        self.assertEqual(absorbed["released_dependency_ids"], [])
