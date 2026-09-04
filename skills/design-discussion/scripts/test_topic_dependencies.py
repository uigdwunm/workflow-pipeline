"""Public CLI Ticket-07 dependency matrix, separated from legacy evolution tests."""

from __future__ import annotations

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
