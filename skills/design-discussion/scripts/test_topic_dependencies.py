"""Public CLI Ticket-07 dependency matrix, separated from legacy evolution tests."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
import importlib
import sys
import unittest
import uuid

sys.path.insert(0, str(Path(__file__).parent))


class TopicDependencyCliTests(unittest.TestCase):
    """Reuse the evolution fixture without inheriting its unrelated test methods."""

    def setUp(self) -> None:
        fixture_class = importlib.import_module(
            "test_discussion_protocol"
        ).DiscussionProtocolEvolutionTests
        self.fixture = fixture_class("runTest")
        self.fixture.setUp()

    def tearDown(self) -> None:
        self.fixture.tearDown()

    def test_ticket07_dependency_create_replay_is_cli_idempotent(self) -> None:
        project = self.fixture.make_project("ticket07-dependency-replay", git=False)
        topic = self.fixture.bootstrap_topic(project)
        child = self.fixture.prepare_child_handoff(topic)
        request = self.fixture.evolution_request(
            topic, operation="update-topic-dependency", expected_revision=2,
            expected_topic_revision=1, action="create",
            prerequisite_topic_id=child["target_topic_id"],
            requirement_kind="confirmed-decision", requirement_summary="The API is chosen.",
        )
        code, first, stderr = self.fixture.run_cli(request)
        self.assertEqual(code, 0, stderr)
        ledger = Path(str(topic["ledger_path"]))
        committed = ledger.read_bytes()
        code, replay, stderr = self.fixture.run_cli(request)
        self.assertEqual(code, 0, stderr)
        self.assertTrue(replay["idempotent_replay"])
        self.assertEqual(replay["dependency_id"], first["dependency_id"])
        self.assertEqual(ledger.read_bytes(), committed)

    def test_ticket07_phase2_dependency_history_is_immutable_via_cli(self) -> None:
        project = self.fixture.make_project("ticket07-phase2-cutoff", git=False)
        topic = self.fixture.bootstrap_topic(project)
        child = self.fixture.prepare_child_handoff(topic)
        code, _, stderr = self.fixture.run_cli(self.fixture.evolution_request(
            topic, operation="update-topic-dependency", expected_revision=2,
            expected_topic_revision=1, action="create", prerequisite_topic_id=child["target_topic_id"],
            requirement_kind="confirmed-decision", requirement_summary="The child decides the API."))
        self.assertEqual(code, 0, stderr)
        ledger = Path(str(topic["ledger_path"]))
        self.fixture.rewrite_ledger_with_valid_digest(ledger, "current_phase: 0", "current_phase: 2")
        before = ledger.read_bytes()
        code, rejected, _ = self.fixture.run_cli(self.fixture.evolution_request(
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
        project = self.fixture.make_project("ticket07-direct-invalidation", git=False)
        topic = self.fixture.bootstrap_topic(project)
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
            request = self.fixture.evolution_request(topic, operation=operation, **values)
            request["actor_topic_id"] = actor
            request["actor_conversation_ref"] = owner
            return request

        for actor, owner, dependency_id, decision_id, revision in (
            (b_id, "codex-thread:b", b_dependency_id, "D-a", 1),
            (c_id, "codex-thread:c", c_dependency_id, "D-b", 2),
        ):
            code, evaluated, stderr = self.fixture.run_cli(gate_request(actor, owner, "evaluate-topic-gate", basis_selection=[{"dependency_id": dependency_id, "decision_ids": [decision_id]}]))
            self.assertEqual(code, 0, stderr)
            code, released, stderr = self.fixture.run_cli(gate_request(actor, owner, "release-topic-gate", expected_revision=revision, expected_topic_revision=1, release_set=evaluated["release_set"], release_set_sha256=evaluated["release_set_sha256"]))
            self.assertEqual(code, 0, stderr)
            self.assertEqual(released["state"], "open")

        _, before_records = protocol._load_records(ledger)
        before_c = next(item for item in before_records["Topic Dependencies"] if item["dependency_id"] == c_dependency_id)
        changed, ledger_revision, topic_revision = self.fixture.complete_update(project, topic, ledger_revision=3, topic_revision=1, mutation={"type": "change-direction", "summary": "Replace A.", "affected_decision_ids": ["D-a"]})
        update_request = self.fixture.evolution_request(topic, operation="prepare-topic-update", expected_revision=ledger_revision, expected_topic_revision=topic_revision, mutation={"type": "resolve-impact", "impact_id": changed["impact_ids"][0], "decision_id": "D-a", "action": "replace", "summary": "A was replaced."})
        code, update, stderr = self.fixture.run_cli(update_request)
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
        project = self.fixture.make_project("ticket07-phase2-invalidation", git=False)
        topic = self.fixture.bootstrap_topic(project)
        ledger = Path(str(topic["ledger_path"]))
        a_id = str(topic["topic_id"])
        b_id = "topic-" + "b" * 32
        decision = {"decision_id": "D-a", "summary": "Keep A.", "rationale": "Current authority.", "state": "confirmed", "evolution": "confirmed"}
        digest = hashlib.sha256(protocol._canonical_json(decision).encode("utf-8")).hexdigest()
        authority = [{"decision_id": "D-a", "sha256": digest, "summary": "Keep A."}]
        dependency_id = "DEP-" + "3" * 32
        basis = {"basis_version": 1, "dependency_id": dependency_id, "prerequisite_topic_id": a_id, "requirement_kind": "confirmed-decision", "decision_authority": [{"decision_id": "D-a", "sha256": digest}], "authority": {"decision_set_digest": hashlib.sha256(protocol._canonical_json(authority).encode("utf-8")).hexdigest()}}
        frontmatter, records = protocol._load_records(ledger)
        records["Current Topics"].append({"topic_id": b_id, "record_revision": 1, "root_slug": "topic-b", "parent_topic_id": a_id, "current_phase": 2, "phase_state": "active", "review_state": "unreviewed", "topic_state": "open", "topic_document_path": None})
        records["Pending Items"].append({"item_id": "D-a", "item_kind": "decision", "topic_id": a_id, "data_json": protocol._canonical_json(decision)})
        records["Topic Dependencies"].append({"dependency_id": dependency_id, "record_revision": 2, "dependent_topic_id": b_id, "prerequisite_topic_id": a_id, "requirement_kind": "confirmed-decision", "requirement_summary": "A remains current.", "relation_state": "active", "gate_state": "open", "accepted_basis_json": protocol._canonical_json(basis), "gate_reason_json": protocol._canonical_json({"kind": "atomic-release", "release_id": "00000000-0000-4000-8000-000000000000", "ledger_revision": 1})})
        ledger.write_bytes(protocol._render_records_ledger(frontmatter, records))
        _, before_records = protocol._load_records(ledger)
        before_dependency = next(item for item in before_records["Topic Dependencies"] if item["dependency_id"] == dependency_id)
        changed, ledger_revision, topic_revision = self.fixture.complete_update(project, topic, ledger_revision=1, topic_revision=1, mutation={"type": "change-direction", "summary": "Replace A.", "affected_decision_ids": ["D-a"]})
        code, _, stderr = self.fixture.run_cli(self.fixture.evolution_request(topic, operation="prepare-topic-update", expected_revision=ledger_revision, expected_topic_revision=topic_revision, mutation={"type": "resolve-impact", "impact_id": changed["impact_ids"][0], "decision_id": "D-a", "action": "replace", "summary": "A was replaced."}))
        self.assertEqual(code, 0, stderr)
        _, after_records = protocol._load_records(ledger)
        self.assertEqual(next(item for item in after_records["Topic Dependencies"] if item["dependency_id"] == dependency_id), before_dependency)

    def test_ticket07_child_result_requires_selection_when_current_authority_exists(self) -> None:
        project = self.fixture.make_project("ticket07-child-result-authority", git=False)
        topic = self.fixture.bootstrap_topic(project)
        prepared, child_ref = self.fixture.activate_child_handoff(topic)
        self.fixture.complete_update(project, topic, ledger_revision=5, topic_revision=1, mutation={"type": "confirm-decision", "summary": "Use typed requests.", "rationale": "The child has selected its public authority."})
        ledger = Path(str(topic["ledger_path"]))
        pending_marker = "## Pending Items\n\n"
        current = ledger.read_text(encoding="utf-8")
        pending_start = current.index(pending_marker)
        pending_end = current.index("\n## ", pending_start + len(pending_marker))
        pending = current[pending_start:pending_end]
        self.fixture.rewrite_ledger_with_valid_digest(ledger, pending, pending.replace(str(topic["topic_id"]), str(prepared["target_topic_id"])))
        before = ledger.read_bytes()
        request = self.fixture.handoff_request(topic, operation="submit-child-result", ledger_revision=7, topic_revision=1, owner_ref=child_ref, handoff_id=prepared["handoff_id"], attempt_id=prepared["attempt_id"], result_scope=["api"], summary="Use typed requests.")
        request["actor_topic_id"] = prepared["target_topic_id"]
        code, rejected, _ = self.fixture.run_cli(request)
        self.assertEqual(code, 1)
        self.assertEqual(rejected["error"]["code"], "topic_dependency_authority_selection_required")
        self.assertEqual(ledger.read_bytes(), before)

    def test_ticket07_dependency_request_type_and_summary_bounds_via_cli(self) -> None:
        project = self.fixture.make_project("ticket07-dependency-request-bounds", git=False)
        topic = self.fixture.bootstrap_topic(project)
        child = self.fixture.prepare_child_handoff(topic)
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
                request = self.fixture.evolution_request(
                    topic, operation="update-topic-dependency", **{**common, field: value}
                )
                code, rejected, _ = self.fixture.run_cli(request)
                self.assertEqual(code, 1)
                self.assertEqual(rejected["error"]["code"], "invalid_request")
                self.assertEqual(ledger.read_bytes(), before)

    def test_ticket07_dependency_error_context_matrix_via_cli(self) -> None:
        """Dependency failures name the affected edge and never partially write."""
        protocol = importlib.import_module("discussion_protocol")
        project = self.fixture.make_project("ticket07-dependency-error-context", git=False)
        topic = self.fixture.bootstrap_topic(project)
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
        create = self.fixture.evolution_request(
            topic, operation="update-topic-dependency", expected_revision=1,
            expected_topic_revision=1, action="create",
            prerequisite_topic_id=prerequisite_id,
            requirement_kind="confirmed-decision", requirement_summary="Await the prerequisite decision.",
        )
        code, created, stderr = self.fixture.run_cli(create)
        self.assertEqual(code, 0, stderr)
        dependency_id = created["dependency_id"]
        committed = ledger.read_bytes()

        def reject(request: dict[str, object], code_name: str, context: dict[str, object]) -> None:
            with self.subTest(code=code_name):
                code, response, _ = self.fixture.run_cli(request)
                self.assertEqual(code, 1)
                self.assertEqual(response["error"]["code"], code_name)
                self.assertEqual(response["error"]["context"], context)
                self.assertEqual(ledger.read_bytes(), committed)

        reject(self.fixture.evolution_request(
            topic, operation="update-topic-dependency", expected_revision=2,
            expected_topic_revision=1, action="cancel", dependency_id=dependency_id,
            expected_dependency_revision=99,
        ), "record_revision_conflict", {
            "dependency_id": dependency_id, "dependent_topic_id": dependent_id,
            "prerequisite_topic_id": prerequisite_id,
            "expected_dependency_revision": 99, "actual_dependency_revision": 1,
        })
        reject(self.fixture.evolution_request(
            topic, operation="update-topic-dependency", expected_revision=2,
            expected_topic_revision=1, action="create",
            prerequisite_topic_id=prerequisite_id, requirement_kind="confirmed-decision",
            requirement_summary="The duplicate edge.",
        ), "topic_dependency_duplicate", {
            "dependent_topic_id": dependent_id, "prerequisite_topic_id": prerequisite_id,
        })
        wrong_owner = self.fixture.evolution_request(
            topic, operation="update-topic-dependency", expected_revision=2,
            expected_topic_revision=1, action="cancel", dependency_id=dependency_id,
            expected_dependency_revision=1,
        )
        wrong_owner.update({"actor_topic_id": prerequisite_id, "actor_conversation_ref": "codex-thread:prerequisite"})
        reject(wrong_owner, "topic_dependency_ownership_conflict", {
            "dependency_id": dependency_id, "dependent_topic_id": dependent_id,
            "prerequisite_topic_id": prerequisite_id,
        })
        cycle = self.fixture.evolution_request(
            topic, operation="update-topic-dependency", expected_revision=2,
            expected_topic_revision=1, action="create",
            prerequisite_topic_id=dependent_id, requirement_kind="confirmed-decision",
            requirement_summary="This would make a cycle.",
        )
        cycle.update({"actor_topic_id": prerequisite_id, "actor_conversation_ref": "codex-thread:prerequisite"})
        reject(cycle, "topic_dependency_cycle", {
            "dependent_topic_id": prerequisite_id, "prerequisite_topic_id": dependent_id,
        })
        evidence = self.fixture.evolution_request(
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
        project = self.fixture.make_project("ticket07-dependency-persisted-shape", git=False)
        topic = self.fixture.bootstrap_topic(project)
        child = self.fixture.prepare_child_handoff(topic)
        ledger = Path(str(topic["ledger_path"]))
        code, _, stderr = self.fixture.run_cli(self.fixture.evolution_request(
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
        code, response, _ = self.fixture.run_cli(
            self.fixture.evolution_request(topic, operation="read-topic")
        )
        self.assertEqual(code, 1)
        self.assertEqual(response["error"]["code"], "state_corrupt")
        self.assertNotEqual(response["error"]["code"], "internal_error")
        self.assertEqual(ledger.read_bytes(), malformed)

    def test_ticket07_oversized_persisted_dependency_summary_is_state_corrupt_via_cli(self) -> None:
        """A ledger-only summary bound is enforced before any read response."""
        protocol = importlib.import_module("discussion_protocol")
        project = self.fixture.make_project("ticket07-oversized-persisted-summary", git=False)
        topic = self.fixture.bootstrap_topic(project)
        child = self.fixture.prepare_child_handoff(topic)
        ledger = Path(str(topic["ledger_path"]))
        code, _, stderr = self.fixture.run_cli(self.fixture.evolution_request(
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
        code, response, _ = self.fixture.run_cli(
            self.fixture.evolution_request(topic, operation="read-topic")
        )
        self.assertEqual(code, 1)
        self.assertEqual(response["error"]["code"], "state_corrupt")
        self.assertEqual(ledger.read_bytes(), malformed)
