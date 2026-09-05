"""Public CLI Ticket-07 Phase-0/1 gate entrypoint scenarios."""

from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).parent))

import discussion_protocol as PROTOCOL
from discussion_core.topic_dependency_gates import GATE_OPERATION_POLICIES
from test_discussion_protocol import hashlib, json, os, subprocess, uuid, ThreadPoolExecutor
from test_topic_dependency_support import (
    DiscussionProtocolScenarioFixture, DiscussionProtocolTestSupport, add_binding, add_closed_dependency, add_topic,
)


class TopicDependencyPhaseGateCliTests(DiscussionProtocolScenarioFixture, DiscussionProtocolTestSupport):
    def test_ticket07_gate_operation_policy_declares_every_boundary(self) -> None:
        enforcing = {
            "discussion-update", "stage-entry-checkpoint", "prepare-handoff",
            "authorize-handoff-discussion", "phase-transition",
        }
        reclosing = {"decision-impact", "checkpoint-broken", "checkpoint-superseded", "phase-reopen"}
        self.assertEqual(set(GATE_OPERATION_POLICIES), enforcing | reclosing)
        for operation in enforcing:
            with self.subTest(operation=operation):
                self.assertEqual(GATE_OPERATION_POLICIES[operation].gate_phases, frozenset({0, 1}))
        for operation in reclosing:
            with self.subTest(operation=operation):
                self.assertTrue(GATE_OPERATION_POLICIES[operation].reclose_directly_affected)

    def test_ticket07_closed_gate_blocks_public_entrypoints_via_cli(self) -> None:
        project = self.make_project("ticket07-closed-entrypoints", git=False)
        topic = self.bootstrap_topic(project)
        prepared = self.prepare_child_handoff(
            topic,
            initial_dependencies=[
                {
                    "dependent_endpoint": "source",
                    "prerequisite_topic_ref": "target",
                    "requirement_kind": "confirmed-decision",
                    "requirement_summary": "The child authority is required.",
                }
            ],
        )
        ledger = Path(str(topic["ledger_path"]))
        before = ledger.read_bytes()
        blocked = [
            self.evolution_request(
                topic,
                operation="prepare-topic-update",
                expected_revision=2,
                expected_topic_revision=1,
                mutation={
                    "type": "confirm-decision",
                    "summary": "A substantive update.",
                    "rationale": "It would change Phase 0 work.",
                },
            ),
            self.checkpoint_request(
                topic,
                operation="prepare-checkpoint",
                ledger_revision=2,
                purpose="stage-entry",
                base_ref="project-root",
            ),
        ]
        blocked.append(self.handoff_request(
            topic, operation="prepare-handoff", ledger_revision=2, handoff_kind="child",
            target_slug="closed-child", scope=["gate"],
            work_snapshot={"goal": "Attempt gated substantive work.", "confirmed_decisions": [], "pending_questions": ["Is the authority current?"]},
            authoritative_references=[{"kind": "checkpoint", "identity": "CP-source", "sha256": "1" * 64}],
        ))
        for request in blocked:
            self.assert_topic_gate_blocked(
                self.run_cli(request),
                ledger=ledger,
                before=before,
                prerequisite_topic_id=str(prepared["target_topic_id"]),
            )
        for to_phase in (1, 2):
            request = self.phase_request(
                topic,
                "prepare-phase-run",
                2,
                from_phase=0,
                to_phase=to_phase,
                route=f"0->{to_phase}",
                carrier_kind="current-topic",
            )
            self.assert_topic_gate_blocked(
                self.run_cli(request),
                ledger=ledger,
                before=before,
                prerequisite_topic_id=str(prepared["target_topic_id"]),
            )

        continuation = self.handoff_request(
            topic, operation="prepare-handoff", ledger_revision=2, handoff_kind="continuation",
            target_slug="closed-continuation", scope=["recovery"],
            work_snapshot={"goal": "Recover the unavailable conversation.", "confirmed_decisions": [], "pending_questions": []},
            authoritative_references=[{"kind": "checkpoint", "identity": "CP-source", "sha256": "1" * 64}],
        )
        code, recovered, stderr = self.run_cli(continuation)
        self.assertEqual(code, 0, stderr)
        self.assertEqual(recovered["state"], "setup-pending")

        phase_one_project = self.make_project("ticket07-closed-one-to-two", git=False)
        phase_one_topic = self.bootstrap_topic(phase_one_project)
        _, revision, topic_revision = self.complete_current_topic_phase(
            phase_one_topic,
            ledger_revision=1,
            topic_revision=1,
            from_phase=0,
            to_phase=1,
        )
        child = self.prepare_child_handoff(
            phase_one_topic,
            ledger_revision=revision,
            topic_revision=topic_revision,
        )
        code, _, stderr = self.run_cli(
            self.evolution_request(
                phase_one_topic,
                operation="update-topic-dependency",
                expected_revision=revision + 1,
                expected_topic_revision=topic_revision,
                action="create",
                prerequisite_topic_id=child["target_topic_id"],
                requirement_kind="confirmed-decision",
                requirement_summary="Phase 1 requires the child authority.",
            )
        )
        self.assertEqual(code, 0, stderr)
        phase_one_ledger = Path(str(phase_one_topic["ledger_path"]))
        phase_one_before = phase_one_ledger.read_bytes()
        self.assert_topic_gate_blocked(
            self.run_cli(
                self.phase_request(
                    phase_one_topic,
                    "prepare-phase-run",
                    revision + 2,
                    topic_revision=topic_revision,
                    from_phase=1,
                    to_phase=2,
                    route="1->2",
                    carrier_kind="current-topic",
                )
            ),
            ledger=phase_one_ledger,
            before=phase_one_before,
            prerequisite_topic_id=str(child["target_topic_id"]),
        )

    def test_closed_gate_continuation_rejects_suspended_question_resolution_without_advancing(self) -> None:
        project = self.make_project("closed-continuation-suspended-resolution", git=False)
        topic = self.bootstrap_topic(project)
        question, ledger_revision, topic_revision = self.complete_update(
            project,
            topic,
            ledger_revision=1,
            topic_revision=1,
            mutation={
                "type": "set-active-question",
                "prompt": "Which continuation owns this question?",
                "recommendation": "Keep the current owner until a child accepts it.",
                "reason": "A continuation must not resolve it through a closed gate.",
            },
        )
        _, ledger_revision, topic_revision = self.complete_update(
            project,
            topic,
            ledger_revision=ledger_revision,
            topic_revision=topic_revision,
            mutation={"type": "insert-idea", "summary": "Split the unresolved question."},
        )
        ledger = Path(str(topic["ledger_path"]))
        frontmatter, records = PROTOCOL._load_records(ledger)
        prerequisite_id = "topic-" + uuid.uuid4().hex
        add_topic(
            records,
            topic_id=prerequisite_id,
            root_slug="closed-prerequisite",
            parent_topic_id=str(topic["topic_id"]),
        )
        add_closed_dependency(
            records,
            dependency_id="DEP-" + uuid.uuid4().hex,
            dependent_topic_id=str(topic["topic_id"]),
            prerequisite_topic_id=prerequisite_id,
            requirement_kind="confirmed-decision",
            requirement_summary="The child authority remains required.",
            gate_reason_json=PROTOCOL._canonical_json({
                "kind": "explicit-create",
                "dependency_update_id": "00000000-0000-4000-8000-000000000000",
                "ledger_revision": ledger_revision,
            }),
        )
        ledger.write_bytes(PROTOCOL._render_records_ledger(frontmatter, records))
        before_ledger = ledger.read_bytes()
        topic_path = Path(str(topic["topic_document_path"]))
        before_document = topic_path.read_bytes()
        request = self.handoff_request(
            topic,
            operation="prepare-handoff",
            ledger_revision=ledger_revision,
            topic_revision=topic_revision,
            handoff_kind="continuation",
            target_slug="closed-continuation",
            scope=["recovery"],
            work_snapshot={"goal": "Recover the current conversation."},
            authoritative_references=[],
            suspended_question_resolution={
                "question_id": question["question_id"],
                "action": "resume",
            },
        )

        code, rejected, _ = self.run_cli(request)

        self.assertEqual(code, 1)
        self.assertEqual(rejected["error"]["code"], "invalid_request")
        self.assertEqual(ledger.read_bytes(), before_ledger)
        self.assertEqual(topic_path.read_bytes(), before_document)

    def test_ticket07_closed_gate_rechecks_ready_and_activation_via_cli(self) -> None:
        for from_phase, to_phase in ((0, 1), (0, 2), (1, 2)):
            for target in ("ready", "active"):
                with self.subTest(route=f"{from_phase}->{to_phase}", target=target):
                    project = self.make_project(
                        f"ticket07-gate-{from_phase}-{to_phase}-{target}", git=False
                    )
                    topic = self.bootstrap_topic(project)
                    revision = 1
                    topic_revision = 1
                    if from_phase == 1:
                        _, revision, topic_revision = self.complete_current_topic_phase(
                            topic,
                            ledger_revision=revision,
                            topic_revision=topic_revision,
                            from_phase=0,
                            to_phase=1,
                        )
                    child = self.prepare_child_handoff(
                        topic,
                        ledger_revision=revision,
                        topic_revision=topic_revision,
                    )
                    revision += 1
                    phase = self.prepare_phase_run(
                        topic,
                        revision=revision,
                        topic_revision=topic_revision,
                        from_phase=from_phase,
                        to_phase=to_phase,
                        carrier_kind="current-topic",
                    )
                    evidence = phase["evidence"]
                    revision += 1
                    code, _, stderr = self.run_cli(
                        self.phase_request(
                            topic,
                            "authorize-phase-carrier",
                            revision,
                            topic_revision=topic_revision,
                            phase_run_id=phase["phase_run_id"],
                            attempt_id=phase["attempt_id"],
                            carrier_ref="discussion-task",
                        )
                    )
                    self.assertEqual(code, 0, stderr)
                    revision += 1
                    if target == "active":
                        code, _, stderr = self.run_cli(
                            self.phase_request(
                                topic,
                                "phase-ready",
                                revision,
                                topic_revision=topic_revision,
                                phase_run_id=phase["phase_run_id"],
                                attempt_id=phase["attempt_id"],
                                carrier_ref="discussion-task",
                                evidence=evidence,
                            )
                        )
                        self.assertEqual(code, 0, stderr)
                        revision += 1
                    code, _, stderr = self.run_cli(
                        self.evolution_request(
                            topic,
                            operation="update-topic-dependency",
                            expected_revision=revision,
                            expected_topic_revision=topic_revision,
                            action="create",
                            prerequisite_topic_id=child["target_topic_id"],
                            requirement_kind="confirmed-decision",
                            requirement_summary="The child authority is required before activation.",
                        )
                    )
                    self.assertEqual(code, 0, stderr)
                    ledger = Path(str(topic["ledger_path"]))
                    before = ledger.read_bytes()
                    operation = "phase-ready" if target == "ready" else "phase-activate"
                    parameters: dict[str, object] = {
                        "phase_run_id": phase["phase_run_id"],
                        "attempt_id": phase["attempt_id"],
                        "evidence": evidence,
                    }
                    if target == "ready":
                        parameters["carrier_ref"] = "discussion-task"
                    self.assert_topic_gate_blocked(
                        self.run_cli(
                            self.phase_request(
                                topic,
                                operation,
                                revision + 1,
                                topic_revision=topic_revision,
                                **parameters,
                            )
                        ),
                        ledger=ledger,
                        before=before,
                        prerequisite_topic_id=str(child["target_topic_id"]),
                    )

    def test_ticket07_closed_gate_allows_acceptance_and_nonadvancing_cli_operations(
        self,
    ) -> None:
        project = self.make_project("ticket07-closed-nonadvancing", git=False)
        topic = self.bootstrap_topic(project)
        prepared = self.prepare_child_handoff(
            topic,
            initial_dependencies=[
                {
                    "dependent_endpoint": "target",
                    "prerequisite_topic_ref": "source",
                    "requirement_kind": "confirmed-decision",
                    "requirement_summary": "The parent authority is required.",
                }
            ],
        )
        child_ref = "codex-thread:closed-child"
        code, _, stderr = self.run_cli(
            self.handoff_request(
                topic,
                operation="bind-handoff",
                ledger_revision=2,
                handoff_id=prepared["handoff_id"],
                attempt_id=prepared["attempt_id"],
                conversation_ref=child_ref,
                verified_identity={
                    "project_id": topic["project_id"],
                    "tree_id": topic["tree_id"],
                    "topic_id": prepared["target_topic_id"],
                    "handoff_id": prepared["handoff_id"],
                    "attempt_id": prepared["attempt_id"],
                    "payload_sha256": prepared["payload_sha256"],
                },
            )
        )
        self.assertEqual(code, 0, stderr)
        accept = self.handoff_request(
            topic,
            operation="accept-handoff",
            ledger_revision=3,
            owner_ref=child_ref,
            handoff_id=prepared["handoff_id"],
            attempt_id=prepared["attempt_id"],
            payload_sha256=prepared["payload_sha256"],
            source_reference_sha256=prepared["authoritative_references_sha256"],
            turn_number=1,
        )
        accept["actor_topic_id"] = prepared["target_topic_id"]
        code, accepted, stderr = self.run_cli(accept)
        self.assertEqual(code, 0, stderr)
        self.assertEqual(accepted["state"], "accepted-awaiting-next-turn")

        gated_project = self.make_project("ticket07-closed-read-recovery", git=False)
        gated_topic = self.bootstrap_topic(gated_project)
        self.prepare_child_handoff(
            gated_topic,
            initial_dependencies=[
                {
                    "dependent_endpoint": "source",
                    "prerequisite_topic_ref": "target",
                    "requirement_kind": "confirmed-decision",
                    "requirement_summary": "The child authority is required.",
                }
            ],
        )
        code, current, stderr = self.run_cli(
            self.evolution_request(gated_topic, operation="read-topic")
        )
        self.assertEqual(code, 0, stderr)
        self.assertEqual(current["derived_gate_state"], "closed")
        code, evaluated, stderr = self.run_cli(
            self.evolution_request(gated_topic, operation="evaluate-topic-gate")
        )
        self.assertEqual(code, 0, stderr)
        self.assertEqual(evaluated["state"], "blocked")
        gated_ledger = Path(str(gated_topic["ledger_path"]))
        before_release = gated_ledger.read_bytes()
        code, rejected_release, stderr = self.run_cli(
            self.evolution_request(
                gated_topic,
                operation="release-topic-gate",
                expected_revision=2,
                expected_topic_revision=1,
                release_set=[],
                release_set_sha256="0" * 64,
            )
        )
        self.assertEqual(code, 1, stderr)
        self.assertNotEqual(rejected_release["error"]["code"], "topic_gate_closed")
        self.assertEqual(gated_ledger.read_bytes(), before_release)
        pause = self.checkpoint_request(
            gated_topic,
            operation="prepare-checkpoint",
            ledger_revision=2,
            purpose="pause",
            base_ref="project-root",
        )
        code, paused, stderr = self.run_cli(pause)
        self.assertEqual(code, 0, stderr)
        self.assertEqual(paused["state"], "prepared")
        code, cancelled, stderr = self.run_cli(
            {
                **self.checkpoint_request(
                    gated_topic,
                    operation="cancel-checkpoint",
                    ledger_revision=3,
                    checkpoint_id=paused["checkpoint_id"],
                    reason="Recover the paused checkpoint.",
                ),
            }
        )
        self.assertEqual(code, 0, stderr)
        self.assertEqual(cancelled["state"], "cancelled")

    def test_ticket07_gate_closure_after_activation_preserves_run_then_phase2_cutoff_via_cli(
        self,
    ) -> None:
        project = self.make_project("ticket07-gate-after-activation", git=False)
        topic = self.bootstrap_topic(project)
        _, revision, topic_revision = self.complete_current_topic_phase(
            topic,
            ledger_revision=1,
            topic_revision=1,
            from_phase=0,
            to_phase=1,
        )
        child = self.prepare_child_handoff(
            topic,
            ledger_revision=revision,
            topic_revision=topic_revision,
        )
        phase = self.prepare_phase_run(
            topic,
            revision=revision + 1,
            topic_revision=topic_revision,
            from_phase=1,
            to_phase=2,
            carrier_kind="current-topic",
        )
        evidence = phase["evidence"]
        revision += 2
        for operation, parameters in (
            ("authorize-phase-carrier", {"carrier_ref": "discussion-task"}),
            ("phase-ready", {"carrier_ref": "discussion-task", "evidence": evidence}),
            ("phase-activate", {"evidence": evidence}),
        ):
            code, _, stderr = self.run_cli(
                self.phase_request(
                    topic,
                    operation,
                    revision,
                    topic_revision=topic_revision,
                    phase_run_id=phase["phase_run_id"],
                    attempt_id=phase["attempt_id"],
                    **parameters,
                )
            )
            self.assertEqual(code, 0, stderr)
            revision += 1
        code, _, stderr = self.run_cli(
            self.evolution_request(
                topic,
                operation="update-topic-dependency",
                expected_revision=revision,
                expected_topic_revision=topic_revision,
                action="create",
                prerequisite_topic_id=child["target_topic_id"],
                requirement_kind="confirmed-decision",
                requirement_summary="The child authority closes after activation.",
            )
        )
        self.assertEqual(code, 0, stderr)
        revision += 1
        for operation, parameters in (
            ("claim-phase-completion", {"carrier_ref": "discussion-task", "evidence": evidence}),
            ("complete-phase-run", {"evidence": evidence}),
            ("finalize-phase-run", {"evidence": evidence}),
        ):
            code, finalized, stderr = self.run_cli(
                self.phase_request(
                    topic,
                    operation,
                    revision,
                    topic_revision=topic_revision,
                    phase_run_id=phase["phase_run_id"],
                    attempt_id=phase["attempt_id"],
                    **parameters,
                )
            )
            self.assertEqual(code, 0, stderr)
            revision += 1
        self.assertEqual(finalized["current_phase"], 2)
        ledger = Path(str(topic["ledger_path"]))
        code, advanced, stderr = self.run_cli(
            self.phase_request(
                topic,
                "prepare-phase-run",
                revision,
                topic_revision=topic_revision + 1,
                from_phase=2,
                to_phase=3,
                route="2->3",
                carrier_kind="current-topic",
            )
        )
        self.assertEqual(code, 0, stderr)
        self.assertEqual(advanced["state"], "prepared")
        after_phase3_prepare = ledger.read_bytes()
        code, rejected, stderr = self.run_cli(
            self.evolution_request(
                topic,
                operation="update-topic-dependency",
                expected_revision=revision + 1,
                expected_topic_revision=topic_revision + 1,
                action="cancel",
                dependency_id=next(
                    item["dependency_id"]
                    for item in self.run_cli(self.evolution_request(topic, operation="read-topic"))[1]["topic_dependencies"]
                ),
                expected_dependency_revision=1,
            )
        )
        self.assertEqual(code, 1, stderr)
        self.assertEqual(rejected["error"]["code"], "topic_dependency_phase_conflict")
        self.assertEqual(ledger.read_bytes(), after_phase3_prepare)

    def test_ticket07_closed_gate_blocks_phase1_no_code_integration_via_cli(self) -> None:
        project = self.make_project("ticket07-no-code-gate", git=False)
        topic = self.bootstrap_topic(project)
        ledger = Path(str(topic["ledger_path"]))
        frontmatter, records = PROTOCOL._load_records(ledger)
        records["Current Topics"][0]["current_phase"] = 1
        prerequisite_id = "topic-" + uuid.uuid4().hex
        dependency_id = "DEP-" + uuid.uuid4().hex
        add_topic(records, topic_id=prerequisite_id, root_slug="prerequisite", parent_topic_id=None)
        add_binding(records, topic_id=prerequisite_id, conversation_ref="codex-thread:prerequisite")
        add_closed_dependency(records, dependency_id=dependency_id, dependent_topic_id=topic["topic_id"],
            prerequisite_topic_id=prerequisite_id, requirement_kind="confirmed-decision",
            requirement_summary="The prerequisite authority is required.", gate_reason_json=PROTOCOL._canonical_json({"kind": "explicit-create", "dependency_update_id": "00000000-0000-4000-8000-000000000000", "ledger_revision": 1}))
        ledger.write_bytes(PROTOCOL._render_records_ledger(frontmatter, records))
        before = ledger.read_bytes()
        request = self.phase_request(
            topic, "prepare-no-code-integration-run", 1,
            source_checkpoint_id="CP-" + "a" * 32, scope=["api"],
            absorbed_relation_ids=["R-" + "a" * 32], child_phase_result_ids=["PH-00000000"],
        )
        code, rejected, _ = self.run_cli(request)
        self.assertEqual(code, 1)
        self.assertEqual(rejected["error"]["code"], "topic_gate_closed", rejected)
        self.assertEqual(ledger.read_bytes(), before)

    def test_ticket07_nested_gate_payload_shapes_fail_stably_via_cli(self) -> None:
        project = self.make_project("ticket07-nested-gate-payloads", git=False)
        parent = self.bootstrap_topic(project)
        handoff, child_ref = self.activate_child_handoff(parent)
        child = {**parent, "topic_id": handoff["target_topic_id"]}
        ledger = Path(str(parent["ledger_path"]))
        frontmatter, records = PROTOCOL._load_records(ledger)
        dependency_id = "DEP-" + uuid.uuid4().hex
        records["Topic Dependencies"].append({"dependency_id": dependency_id, "record_revision": 1, "dependent_topic_id": child["topic_id"], "prerequisite_topic_id": parent["topic_id"], "requirement_kind": "confirmed-decision", "requirement_summary": "A selection is required.", "relation_state": "active", "gate_state": "closed", "accepted_basis_json": None, "gate_reason_json": PROTOCOL._canonical_json({"kind": "explicit-create", "dependency_update_id": "00000000-0000-4000-8000-000000000000", "ledger_revision": 1})})
        ledger.write_bytes(PROTOCOL._render_records_ledger(frontmatter, records))
        malformed = [
            {}, None, "selection", [[dependency_id]],
            [{"dependency_id": dependency_id, "decision_ids": [], "extra": True}],
        ]
        for payload in malformed:
            with self.subTest(payload=repr(payload)):
                request = self.evolution_request(
                    child, operation="evaluate-topic-gate", owner_ref=child_ref,
                    basis_selection=payload,
                )
                code, rejected, _ = self.run_cli(request)
                self.assertEqual(code, 1)
                self.assertEqual(rejected["error"]["code"], "invalid_request")
        for payload in ({}, None, "release", [[dependency_id]]):
            with self.subTest(release_payload=repr(payload)):
                request = self.evolution_request(
                    child, operation="release-topic-gate", expected_revision=5,
                    expected_topic_revision=1, owner_ref=child_ref,
                    release_set=payload, release_set_sha256="0" * 64,
                )
                code, rejected, _ = self.run_cli(request)
                self.assertEqual(code, 1)
                self.assertEqual(rejected["error"]["code"], "invalid_request")


if __name__ == "__main__":
    unittest.main()
