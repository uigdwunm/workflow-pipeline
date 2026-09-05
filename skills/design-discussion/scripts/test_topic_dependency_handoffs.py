"""Public CLI Ticket-07 handoff and absorption scenarios."""

from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).parent))

import discussion_protocol as PROTOCOL
from test_discussion_protocol import hashlib, json, os, subprocess, uuid, ThreadPoolExecutor
from test_topic_dependency_support import TopicDependencyScenarioTest


class TopicDependencyHandoffCliTests(TopicDependencyScenarioTest):
    def test_ticket07_pending_child_impact_blocks_gate_until_acceptance_via_cli(self) -> None:
        project = self.fixture.make_project("ticket07-impact-gate", git=False)
        topic = self.fixture.bootstrap_topic(project)
        prepared, child_ref = self.fixture.activate_child_handoff(
            topic,
            initial_dependencies=[{
                "dependent_endpoint": "source", "prerequisite_topic_ref": "target",
                "requirement_kind": "confirmed-decision",
                "requirement_summary": "The child authority is required.",
            }],
        )
        ledger = Path(str(topic["ledger_path"]))
        frontmatter, records = PROTOCOL._load_records(ledger)
        decision = {
            "decision_id": "D-child", "summary": "Typed.", "rationale": "Current.",
            "state": "confirmed", "evolution": "confirmed",
        }
        records["Pending Items"].append({
            "item_id": "D-child", "item_kind": "decision",
            "topic_id": prepared["target_topic_id"],
            "data_json": PROTOCOL._canonical_json(decision),
        })
        ledger.write_bytes(PROTOCOL._render_records_ledger(frontmatter, records))
        submit = self.fixture.handoff_request(
            topic, operation="submit-child-result", ledger_revision=5, owner_ref=child_ref,
            handoff_id=prepared["handoff_id"], attempt_id=prepared["attempt_id"],
            result_scope=["api"], summary="Typed.", authority_selection={
                "authority_kind": "confirmed-decision", "authority_identity": None,
                "decision_ids": ["D-child"],
            },
        )
        submit["actor_topic_id"] = prepared["target_topic_id"]
        code, claimed, stderr = self.fixture.run_cli(submit)
        self.fixture.assertEqual(code, 0, stderr)
        dependency_id = prepared["initial_dependencies"][0]["dependency_id"]
        code, before_impact, stderr = self.fixture.run_cli(self.fixture.evolution_request(
            topic, operation="evaluate-topic-gate", basis_selection=[{
                "dependency_id": dependency_id, "decision_ids": ["D-child"],
            }],
        ))
        self.fixture.assertEqual(code, 0, stderr)
        self.fixture.assertEqual(before_impact["state"], "releasable")
        code, pending, stderr = self.fixture.run_cli(self.fixture.handoff_request(
            topic, operation="record-child-result", ledger_revision=6,
            handoff_id=prepared["handoff_id"], child_result_id=claimed["child_result_id"],
            effect="impact",
        ))
        self.fixture.assertEqual(code, 0, stderr)
        self.fixture.assertEqual(pending["state"], "pending-impact")
        evaluation_request = self.fixture.evolution_request(
            topic, operation="evaluate-topic-gate", basis_selection=[{
                "dependency_id": dependency_id, "decision_ids": ["D-child"],
            }],
        )
        code, evaluation, stderr = self.fixture.run_cli(evaluation_request)
        self.fixture.assertEqual(code, 0, stderr)
        self.fixture.assertEqual(evaluation["state"], "blocked")
        self.fixture.assertEqual(evaluation["dependencies"][0]["pending_impact_ids"], [pending["impact_id"]])
        before = ledger.read_bytes()
        code, rejected, _ = self.fixture.run_cli(self.fixture.evolution_request(
            topic, operation="release-topic-gate", expected_revision=7,
            expected_topic_revision=1, release_set=before_impact["release_set"],
            release_set_sha256=before_impact["release_set_sha256"],
        ))
        self.fixture.assertEqual(code, 1)
        self.fixture.assertEqual(rejected["error"]["code"], "topic_gate_evaluation_stale")
        self.fixture.assertEqual(ledger.read_bytes(), before)
        code, accepted, stderr = self.fixture.run_cli(self.fixture.evolution_request(
            topic, operation="prepare-topic-update", expected_revision=7,
            expected_topic_revision=1, mutation={
                "type": "resolve-impact", "impact_id": pending["impact_id"],
                "action": "accept", "summary": "The parent accepts the child impact.",
            },
        ))
        self.fixture.assertEqual(code, 0, stderr)
        self.fixture.assertEqual(accepted["impact_action"], "accept")
        code, evaluated, stderr = self.fixture.run_cli(self.fixture.evolution_request(
            topic, operation="evaluate-topic-gate", basis_selection=[{
                "dependency_id": dependency_id, "decision_ids": ["D-child"],
            }],
        ))
        self.fixture.assertEqual(code, 0, stderr)
        self.fixture.assertEqual(evaluated["state"], "releasable")
        code, released, stderr = self.fixture.run_cli(self.fixture.evolution_request(
            topic, operation="release-topic-gate", expected_revision=8,
            expected_topic_revision=2, release_set=evaluated["release_set"],
            release_set_sha256=evaluated["release_set_sha256"],
        ))
        self.fixture.assertEqual(code, 0, stderr)
        self.fixture.assertEqual(released["state"], "open")

    def test_ticket07_absorb_release_replace_preserves_historical_child_basis_via_cli(self) -> None:
        project = self.fixture.make_project("ticket07-absorb-replace-history", git=False)
        topic = self.fixture.bootstrap_topic(project)
        prepared, child_ref = self.fixture.activate_child_handoff(topic, initial_dependencies=[{"dependent_endpoint": "source", "prerequisite_topic_ref": "target", "requirement_kind": "confirmed-decision", "requirement_summary": "Child decides."}])
        ledger = Path(str(topic["ledger_path"]))
        frontmatter, records = PROTOCOL._load_records(ledger)
        decision = {"decision_id": "D-child", "summary": "Typed.", "rationale": "Current.", "state": "confirmed", "evolution": "confirmed"}
        records["Pending Items"].append({"item_id": "D-child", "item_kind": "decision", "topic_id": prepared["target_topic_id"], "data_json": PROTOCOL._canonical_json(decision)})
        ledger.write_bytes(PROTOCOL._render_records_ledger(frontmatter, records))
        submit = self.fixture.handoff_request(topic, operation="submit-child-result", ledger_revision=5, owner_ref=child_ref, handoff_id=prepared["handoff_id"], attempt_id=prepared["attempt_id"], result_scope=["api"], summary="Typed.", authority_selection={"authority_kind": "confirmed-decision", "authority_identity": None, "decision_ids": ["D-child"]})
        submit["actor_topic_id"] = prepared["target_topic_id"]
        code, claimed, stderr = self.fixture.run_cli(submit)
        self.fixture.assertEqual(code, 0, stderr)
        dependency_id = prepared["initial_dependencies"][0]["dependency_id"]
        release = {"dependency_id": dependency_id, "authority_kind": "confirmed-decision", "authority_identity": None, "decision_ids": ["D-child"]}
        code, _, stderr = self.fixture.run_cli(self.fixture.handoff_request(topic, operation="record-child-result", ledger_revision=6, handoff_id=prepared["handoff_id"], child_result_id=claimed["child_result_id"], effect="absorb", dependency_releases=[release]))
        self.fixture.assertEqual(code, 0, stderr)
        frontmatter, records = PROTOCOL._load_records(ledger)
        second = "topic-" + "e" * 32
        records["Current Topics"].append({"topic_id": second, "record_revision": 1, "root_slug": "second", "parent_topic_id": str(topic["topic_id"]), "current_phase": 1, "phase_state": "active", "review_state": "unreviewed", "topic_state": "open", "topic_document_path": None})
        ledger.write_bytes(PROTOCOL._render_records_ledger(frontmatter, records))
        code, _, stderr = self.fixture.run_cli(self.fixture.evolution_request(topic, operation="update-topic-dependency", expected_revision=7, expected_topic_revision=1, action="replace", dependency_id=dependency_id, expected_dependency_revision=2, prerequisite_topic_id=second, requirement_kind="phase-1-result", requirement_summary="Second result."))
        self.fixture.assertEqual(code, 0, stderr)
        code, read, stderr = self.fixture.run_cli(self.fixture.evolution_request(topic, operation="read-topic"))
        self.fixture.assertEqual(code, 0, stderr)
        dependency = next(item for item in read["topic_dependencies"] if item["dependency_id"] == dependency_id)
        self.fixture.assertEqual(dependency["gate_state"], "closed")
        self.fixture.assertEqual(json.loads(str(dependency["accepted_basis_json"]))["prerequisite_topic_id"], prepared["target_topic_id"])

    def test_ticket07_initial_dependency_prepare_failure_retries_and_binds_via_cli(self) -> None:
        project = self.fixture.make_project("ticket07-initial-dependency-prepare-failure", git=False)
        topic = self.fixture.bootstrap_topic(project)
        initial_dependencies = [{
            "dependent_endpoint": "source", "prerequisite_topic_ref": "target",
            "requirement_kind": "confirmed-decision",
            "requirement_summary": "The child selects the API.",
        }]
        request = self.fixture.handoff_request(
            topic, operation="prepare-handoff", ledger_revision=1,
            handoff_kind="child", target_slug="api-shape", scope=["api"],
            work_snapshot={"goal": "Choose the API.", "confirmed_decisions": [], "pending_questions": ["Which API?"]},
            authoritative_references=[{"kind": "checkpoint", "identity": "CP-source", "sha256": "1" * 64}],
            initial_dependencies=initial_dependencies,
        )
        ledger = Path(str(topic["ledger_path"]))
        before = ledger.read_bytes()
        code, failed, _ = self.fixture.run_cli(
            request, failpoint="handoff-after-initial-dependencies-before-ledger-write"
        )
        self.fixture.assertEqual(code, 1)
        self.fixture.assertEqual(failed["error"]["code"], "injected_failure")
        self.fixture.assertEqual(ledger.read_bytes(), before)
        code, prepared, stderr = self.fixture.run_cli(request)
        self.fixture.assertEqual(code, 0, stderr)
        self.fixture.assertEqual(len(prepared["initial_dependencies"]), 1)
        dependency_id = prepared["initial_dependencies"][0]["dependency_id"]
        self.fixture.assertRegex(dependency_id, r"^DEP-[0-9a-f]{32}$")
        code, replay, stderr = self.fixture.run_cli(request)
        self.fixture.assertEqual(code, 0, stderr)
        self.fixture.assertTrue(replay["idempotent_replay"])
        self.fixture.assertEqual(replay["initial_dependencies"], prepared["initial_dependencies"])
        bind = self.fixture.handoff_request(
            topic, operation="bind-handoff", ledger_revision=2,
            handoff_id=prepared["handoff_id"], attempt_id=prepared["attempt_id"],
            conversation_ref="codex-thread:late-bound-child", verified_identity={
                "project_id": topic["project_id"], "tree_id": topic["tree_id"],
                "topic_id": prepared["target_topic_id"], "handoff_id": prepared["handoff_id"],
                "attempt_id": prepared["attempt_id"], "payload_sha256": prepared["payload_sha256"],
            },
        )
        code, bound, stderr = self.fixture.run_cli(bind)
        self.fixture.assertEqual(code, 0, stderr)
        code, duplicate_bind, stderr = self.fixture.run_cli(bind)
        self.fixture.assertEqual(code, 0, stderr)
        self.fixture.assertTrue(duplicate_bind["idempotent_replay"])
        self.fixture.assertEqual(duplicate_bind["attempt_id"], bound["attempt_id"])
        _, records = PROTOCOL._load_records(ledger)
        self.fixture.assertEqual(
            [item["dependency_id"] for item in records["Topic Dependencies"]], [dependency_id]
        )

    def test_ticket07_duplicate_absorb_release_is_atomic_via_cli(self) -> None:
        project = self.fixture.make_project("ticket07-duplicate-absorb-release", git=False)
        topic = self.fixture.bootstrap_topic(project)
        prepared, child_ref = self.fixture.activate_child_handoff(
            topic,
            initial_dependencies=[{
                "dependent_endpoint": "source", "prerequisite_topic_ref": "target",
                "requirement_kind": "confirmed-decision",
                "requirement_summary": "The child selects the API.",
            }],
        )
        ledger = Path(str(topic["ledger_path"]))
        frontmatter, records = PROTOCOL._load_records(ledger)
        child_decision = {
            "decision_id": "D-child", "summary": "Use typed requests.",
            "rationale": "The child selected the API.", "state": "confirmed",
            "evolution": "confirmed",
        }
        records["Pending Items"].append({
            "item_id": "D-child", "item_kind": "decision",
            "topic_id": prepared["target_topic_id"],
            "data_json": PROTOCOL._canonical_json(child_decision),
        })
        ledger.write_bytes(PROTOCOL._render_records_ledger(frontmatter, records))
        submit = self.fixture.handoff_request(
            topic, operation="submit-child-result", ledger_revision=5,
            owner_ref=child_ref, handoff_id=prepared["handoff_id"],
            attempt_id=prepared["attempt_id"], result_scope=["api"],
            summary="Use typed requests.", authority_selection={
                "authority_kind": "confirmed-decision", "authority_identity": None,
                "decision_ids": ["D-child"],
            },
        )
        submit["actor_topic_id"] = prepared["target_topic_id"]
        code, claimed, stderr = self.fixture.run_cli(submit)
        self.fixture.assertEqual(code, 0, stderr)
        dependency_id = prepared["initial_dependencies"][0]["dependency_id"]
        release = {
            "dependency_id": dependency_id, "authority_kind": "confirmed-decision",
            "authority_identity": None, "decision_ids": ["D-child"],
        }
        duplicate = self.fixture.handoff_request(
            topic, operation="record-child-result", ledger_revision=6,
            handoff_id=prepared["handoff_id"], child_result_id=claimed["child_result_id"],
            effect="absorb", dependency_releases=[release, release],
        )
        before = ledger.read_bytes()
        code, rejected, _ = self.fixture.run_cli(duplicate)
        self.fixture.assertEqual(code, 1)
        self.fixture.assertEqual(rejected["error"]["code"], "invalid_request")
        self.fixture.assertEqual(ledger.read_bytes(), before)
        code, handoff, stderr = self.fixture.run_cli(self.fixture.handoff_request(
            topic, operation="read-handoff", handoff_id=prepared["handoff_id"],
        ))
        self.fixture.assertEqual(code, 0, stderr)
        self.fixture.assertEqual(handoff["handoff"]["record_revision"], 4)
        self.fixture.assertEqual(handoff["attempts"][0]["state"], "active")

    def test_ticket07_injected_absorb_release_is_atomic_via_cli(self) -> None:
        project = self.fixture.make_project("ticket07-absorb-release-fault", git=False)
        topic = self.fixture.bootstrap_topic(project)
        prepared, child_ref = self.fixture.activate_child_handoff(
            topic,
            initial_dependencies=[{
                "dependent_endpoint": "source", "prerequisite_topic_ref": "target",
                "requirement_kind": "confirmed-decision",
                "requirement_summary": "The child selects the API.",
            }],
        )
        ledger = Path(str(topic["ledger_path"]))
        frontmatter, records = PROTOCOL._load_records(ledger)
        decision = {"decision_id": "D-child", "summary": "Use typed requests.", "rationale": "The child selected the API.", "state": "confirmed", "evolution": "confirmed"}
        records["Pending Items"].append({"item_id": "D-child", "item_kind": "decision", "topic_id": prepared["target_topic_id"], "data_json": PROTOCOL._canonical_json(decision)})
        ledger.write_bytes(PROTOCOL._render_records_ledger(frontmatter, records))
        submit = self.fixture.handoff_request(
            topic, operation="submit-child-result", ledger_revision=5, owner_ref=child_ref,
            handoff_id=prepared["handoff_id"], attempt_id=prepared["attempt_id"],
            result_scope=["api"], summary="Use typed requests.", authority_selection={"authority_kind": "confirmed-decision", "authority_identity": None, "decision_ids": ["D-child"]},
        )
        submit["actor_topic_id"] = prepared["target_topic_id"]
        code, claimed, stderr = self.fixture.run_cli(submit)
        self.fixture.assertEqual(code, 0, stderr)
        release = {"dependency_id": prepared["initial_dependencies"][0]["dependency_id"], "authority_kind": "confirmed-decision", "authority_identity": None, "decision_ids": ["D-child"]}
        absorb = self.fixture.handoff_request(
            topic, operation="record-child-result", ledger_revision=6,
            handoff_id=prepared["handoff_id"], child_result_id=claimed["child_result_id"],
            effect="absorb", dependency_releases=[release],
        )
        before = ledger.read_bytes()
        code, failed, _ = self.fixture.run_cli(absorb, failpoint="child-result-absorb-before-ledger-write")
        self.fixture.assertEqual(code, 1)
        self.fixture.assertEqual(failed["error"]["code"], "injected_failure")
        self.fixture.assertEqual(ledger.read_bytes(), before)
        code, absorbed, stderr = self.fixture.run_cli(absorb)
        self.fixture.assertEqual(code, 0, stderr)
        self.fixture.assertEqual(absorbed["released_dependency_ids"], [release["dependency_id"]])
        _, records = PROTOCOL._load_records(ledger)
        dependency = next(item for item in records["Topic Dependencies"] if item["dependency_id"] == release["dependency_id"])
        reason = json.loads(str(dependency["gate_reason_json"]))
        self.fixture.assertEqual(reason["absorb_operation_id"], absorb["idempotency_key"])
        self.fixture.assertEqual(reason["child_result_id"], claimed["child_result_id"])
        code, replay, stderr = self.fixture.run_cli(absorb)
        self.fixture.assertEqual(code, 0, stderr)
        self.fixture.assertTrue(replay["idempotent_replay"])

    def test_ticket07_handoff_crash_recovery_is_cli_idempotent(self) -> None:
        project = self.fixture.make_project("ticket07-handoff-crash-recovery", git=False)
        topic = self.fixture.bootstrap_topic(project)
        prepared = self.fixture.prepare_child_handoff(topic)
        bind = self.fixture.handoff_request(
            topic, operation="bind-handoff", ledger_revision=2,
            handoff_id=prepared["handoff_id"], attempt_id=prepared["attempt_id"],
            conversation_ref="codex-thread:recovered-child", verified_identity={
                "project_id": topic["project_id"], "tree_id": topic["tree_id"],
                "topic_id": prepared["target_topic_id"], "handoff_id": prepared["handoff_id"],
                "attempt_id": prepared["attempt_id"], "payload_sha256": prepared["payload_sha256"],
            },
        )
        ledger = Path(str(topic["ledger_path"]))
        before = ledger.read_bytes()
        code, failed, _ = self.fixture.run_cli(bind, failpoint="handoff-before-binding-ledger-write")
        self.fixture.assertEqual(code, 1)
        self.fixture.assertEqual(failed["error"]["code"], "injected_failure")
        self.fixture.assertEqual(ledger.read_bytes(), before)
        code, bound, stderr = self.fixture.run_cli(bind)
        self.fixture.assertEqual(code, 0, stderr)
        code, replay, stderr = self.fixture.run_cli(bind)
        self.fixture.assertEqual(code, 0, stderr)
        self.fixture.assertTrue(replay["idempotent_replay"])
        self.fixture.assertEqual(replay["attempt_id"], bound["attempt_id"])
