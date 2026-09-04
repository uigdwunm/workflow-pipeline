"""Public CLI Ticket-07 dependency matrix, separated from legacy evolution tests."""

from __future__ import annotations

from pathlib import Path
import importlib
import sys
import unittest

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
