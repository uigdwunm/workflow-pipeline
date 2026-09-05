"""Public CLI Ticket-07 handoff and absorption scenarios."""

from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).parent))

import discussion_protocol as PROTOCOL
from test_discussion_protocol import hashlib, json, os, subprocess, uuid, ThreadPoolExecutor
from test_topic_dependency_support import DiscussionProtocolScenarioFixture, DiscussionProtocolTestSupport


class TopicDependencyHandoffCliTests(DiscussionProtocolScenarioFixture, DiscussionProtocolTestSupport):
    def test_ticket07_child_split_freezes_suspended_question_before_initial_gate_via_cli(self) -> None:
        for dependent_split in (False, True):
            for action in ("resume", "adjust", "invalidate"):
                with self.subTest(dependent_split=dependent_split, action=action):
                    project = self.make_project(
                        f"ticket07-suspended-split-{dependent_split}-{action}", git=False,
                    )
                    topic = self.bootstrap_topic(project)
                    question, ledger_revision, topic_revision = self.complete_update(
                        project, topic, ledger_revision=1, topic_revision=1, mutation={
                            "type": "set-active-question", "prompt": "Which split is safest?",
                            "recommendation": "Keep the parent decision explicit.",
                            "reason": "The child needs a bounded assignment.",
                        },
                    )
                    _, ledger_revision, topic_revision = self.complete_update(
                        project, topic, ledger_revision=ledger_revision,
                        topic_revision=topic_revision,
                        mutation={"type": "insert-idea", "summary": "Split implementation work."},
                    )
                    resolution = {"question_id": question["question_id"], "action": action}
                    if action == "adjust":
                        resolution["adjusted_prompt"] = "Which dependency boundary is safest?"
                    initial_dependencies = ([{
                        "dependent_endpoint": "source", "prerequisite_topic_ref": "target",
                        "requirement_kind": "confirmed-decision",
                        "requirement_summary": "The child conclusion is required first.",
                    }] if dependent_split else None)
                    if dependent_split and action == "resume":
                        before = Path(str(topic["ledger_path"])).read_bytes()
                        failed = self.handoff_request(
                            topic, operation="prepare-handoff", ledger_revision=ledger_revision,
                            topic_revision=topic_revision, handoff_kind="child",
                            target_slug="frozen-question", scope=["api"],
                            work_snapshot={
                                "goal": "Choose the public API shape.",
                                "confirmed_decisions": [],
                                "pending_questions": ["Which requests are public?"],
                            },
                            authoritative_references=[{
                                "kind": "checkpoint", "identity": "CP-source", "sha256": "1" * 64,
                            }],
                            initial_dependencies=initial_dependencies,
                            suspended_question_resolution=resolution,
                        )
                        code, rejected, _ = self.run_cli(
                            failed, failpoint="handoff-after-initial-dependencies-before-ledger-write",
                        )
                        self.assertEqual(code, 1)
                        self.assertEqual(rejected["error"]["code"], "injected_failure")
                        self.assertEqual(Path(str(topic["ledger_path"])).read_bytes(), before)
                    prepared = self.prepare_child_handoff(
                        topic, ledger_revision=ledger_revision, topic_revision=topic_revision,
                        initial_dependencies=initial_dependencies,
                        suspended_question_resolution=resolution,
                    )
                    self.assertEqual(prepared["suspended_question_resolution"], resolution)
                    self.assertEqual(prepared["record_revision"], topic_revision + 1)
                    code, current, stderr = self.run_cli(self.evolution_request(topic, operation="read-topic"))
                    self.assertEqual(code, 0, stderr)
                    resolved = next(item for item in current["questions"] if item["question_id"] == question["question_id"])
                    self.assertEqual(resolved["state"], "invalidated" if action == "invalidate" else "active")
                    if action == "adjust":
                        self.assertEqual(resolved["prompt"], resolution["adjusted_prompt"])
                    self.assertEqual(current["derived_gate_state"], "closed" if dependent_split else "open")

    def test_ticket07_confirmed_child_result_freezes_only_selected_decisions_via_cli(self) -> None:
        project = self.make_project("ticket07-confirmed-narrow-child", git=False)
        topic = self.bootstrap_topic(project)
        prepared, child_ref = self.activate_child_handoff(topic)
        ledger = Path(str(topic["ledger_path"]))
        frontmatter, records = PROTOCOL._load_records(ledger)
        for decision_id in ("D-first", "D-second"):
            decision = {
                "decision_id": decision_id, "summary": decision_id, "rationale": "Current.",
                "state": "confirmed", "evolution": "confirmed",
            }
            records["Pending Items"].append({
                "item_id": decision_id, "item_kind": "decision",
                "topic_id": prepared["target_topic_id"],
                "data_json": PROTOCOL._canonical_json(decision),
            })
        ledger.write_bytes(PROTOCOL._render_records_ledger(frontmatter, records))
        request = self.handoff_request(
            topic, operation="submit-child-result", ledger_revision=5, owner_ref=child_ref,
            handoff_id=prepared["handoff_id"], attempt_id=prepared["attempt_id"],
            result_scope=["api"], summary="First decision.", authority_selection={
                "authority_kind": "confirmed-decision", "authority_identity": None,
                "decision_ids": ["D-first"],
            },
        )
        request["actor_topic_id"] = prepared["target_topic_id"]
        code, claimed, stderr = self.run_cli(request)
        self.assertEqual(code, 0, stderr)
        frozen = claimed["frozen_authority"]
        pairs = frozen["decision_authority"]
        self.assertEqual([pair["decision_id"] for pair in pairs], ["D-first"])
        self.assertEqual(
            frozen["authority"]["decision_set_digest"],
            hashlib.sha256(PROTOCOL._canonical_json(pairs).encode("utf-8")).hexdigest(),
        )

    def test_ticket07_pending_child_impact_blocks_gate_until_acceptance_via_cli(self) -> None:
        project = self.make_project("ticket07-impact-gate", git=False)
        topic = self.bootstrap_topic(project)
        prepared, child_ref = self.activate_child_handoff(
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
        submit = self.handoff_request(
            topic, operation="submit-child-result", ledger_revision=5, owner_ref=child_ref,
            handoff_id=prepared["handoff_id"], attempt_id=prepared["attempt_id"],
            result_scope=["api"], summary="Typed.", authority_selection={
                "authority_kind": "confirmed-decision", "authority_identity": None,
                "decision_ids": ["D-child"],
            },
        )
        submit["actor_topic_id"] = prepared["target_topic_id"]
        code, claimed, stderr = self.run_cli(submit)
        self.assertEqual(code, 0, stderr)
        dependency_id = prepared["initial_dependencies"][0]["dependency_id"]
        code, before_impact, stderr = self.run_cli(self.evolution_request(
            topic, operation="evaluate-topic-gate", basis_selection=[{
                "dependency_id": dependency_id, "decision_ids": ["D-child"],
            }],
        ))
        self.assertEqual(code, 0, stderr)
        self.assertEqual(before_impact["state"], "releasable")
        code, pending, stderr = self.run_cli(self.handoff_request(
            topic, operation="record-child-result", ledger_revision=6,
            handoff_id=prepared["handoff_id"], child_result_id=claimed["child_result_id"],
            effect="impact",
        ))
        self.assertEqual(code, 0, stderr)
        self.assertEqual(pending["state"], "pending-impact")
        evaluation_request = self.evolution_request(
            topic, operation="evaluate-topic-gate", basis_selection=[{
                "dependency_id": dependency_id, "decision_ids": ["D-child"],
            }],
        )
        code, evaluation, stderr = self.run_cli(evaluation_request)
        self.assertEqual(code, 0, stderr)
        self.assertEqual(evaluation["state"], "blocked")
        self.assertEqual(evaluation["dependencies"][0]["pending_impact_ids"], [pending["impact_id"]])
        before = ledger.read_bytes()
        code, rejected, _ = self.run_cli(self.evolution_request(
            topic, operation="release-topic-gate", expected_revision=7,
            expected_topic_revision=1, release_set=before_impact["release_set"],
            release_set_sha256=before_impact["release_set_sha256"],
        ))
        self.assertEqual(code, 1)
        self.assertEqual(rejected["error"]["code"], "topic_gate_evaluation_stale")
        self.assertEqual(ledger.read_bytes(), before)
        code, accepted, stderr = self.run_cli(self.evolution_request(
            topic, operation="prepare-topic-update", expected_revision=7,
            expected_topic_revision=1, mutation={
                "type": "resolve-impact", "impact_id": pending["impact_id"],
                "action": "accept", "summary": "The parent accepts the child impact.",
            },
        ))
        self.assertEqual(code, 0, stderr)
        self.assertEqual(accepted["impact_action"], "accept")
        code, evaluated, stderr = self.run_cli(self.evolution_request(
            topic, operation="evaluate-topic-gate", basis_selection=[{
                "dependency_id": dependency_id, "decision_ids": ["D-child"],
            }],
        ))
        self.assertEqual(code, 0, stderr)
        self.assertEqual(evaluated["state"], "releasable")
        code, released, stderr = self.run_cli(self.evolution_request(
            topic, operation="release-topic-gate", expected_revision=8,
            expected_topic_revision=2, release_set=evaluated["release_set"],
            release_set_sha256=evaluated["release_set_sha256"],
        ))
        self.assertEqual(code, 0, stderr)
        self.assertEqual(released["state"], "open")

    def test_ticket07_only_closed_prerequisite_child_impact_bypasses_parent_gate_via_cli(self) -> None:
        project = self.make_project("ticket07-impact-source-gate", git=False)
        topic = self.bootstrap_topic(project)
        ledger = Path(str(topic["ledger_path"]))

        def activate(prepared: dict[str, object], child_ref: str, revision: int) -> int:
            code, _, stderr = self.run_cli(self.handoff_request(
                topic, operation="bind-handoff", ledger_revision=revision,
                handoff_id=prepared["handoff_id"], attempt_id=prepared["attempt_id"],
                conversation_ref=child_ref, verified_identity={
                    "project_id": topic["project_id"], "tree_id": topic["tree_id"],
                    "topic_id": prepared["target_topic_id"], "handoff_id": prepared["handoff_id"],
                    "attempt_id": prepared["attempt_id"], "payload_sha256": prepared["payload_sha256"],
                },
            ))
            self.assertEqual(code, 0, stderr)
            accept = self.handoff_request(
                topic, operation="accept-handoff", ledger_revision=revision + 1,
                owner_ref=child_ref, handoff_id=prepared["handoff_id"],
                attempt_id=prepared["attempt_id"], payload_sha256=prepared["payload_sha256"],
                source_reference_sha256=prepared["authoritative_references_sha256"], turn_number=1,
            )
            accept["actor_topic_id"] = prepared["target_topic_id"]
            code, _, stderr = self.run_cli(accept)
            self.assertEqual(code, 0, stderr)
            authorize = self.handoff_request(
                topic, operation="authorize-handoff-discussion", ledger_revision=revision + 2,
                owner_ref=child_ref, handoff_id=prepared["handoff_id"],
                attempt_id=prepared["attempt_id"], turn_number=2,
            )
            authorize["actor_topic_id"] = prepared["target_topic_id"]
            code, _, stderr = self.run_cli(authorize)
            self.assertEqual(code, 0, stderr)
            return revision + 3

        unrelated = self.prepare_child_handoff(topic)
        revision = activate(unrelated, "codex-thread:unrelated-child", 2)
        prerequisite = self.prepare_child_handoff(
            topic, ledger_revision=revision,
            initial_dependencies=[{
                "dependent_endpoint": "source", "prerequisite_topic_ref": "target",
                "requirement_kind": "confirmed-decision",
                "requirement_summary": "Only this child may unblock the parent.",
            }],
        )
        revision = activate(prerequisite, "codex-thread:prerequisite-child", revision + 1)
        frontmatter, records = PROTOCOL._load_records(ledger)
        for prepared, decision_id in ((unrelated, "D-unrelated"), (prerequisite, "D-prerequisite")):
            records["Pending Items"].append({
                "item_id": decision_id, "item_kind": "decision",
                "topic_id": prepared["target_topic_id"],
                "data_json": PROTOCOL._canonical_json({
                    "decision_id": decision_id, "summary": decision_id, "rationale": "Current.",
                    "state": "confirmed", "evolution": "confirmed",
                }),
            })
        ledger.write_bytes(PROTOCOL._render_records_ledger(frontmatter, records))

        def submit_and_impact(
            prepared: dict[str, object], child_ref: str, decision_id: str, current_revision: int,
        ) -> tuple[dict[str, object], int]:
            submit = self.handoff_request(
                topic, operation="submit-child-result", ledger_revision=current_revision,
                owner_ref=child_ref, handoff_id=prepared["handoff_id"], attempt_id=prepared["attempt_id"],
                result_scope=["api"], summary=decision_id, authority_selection={
                    "authority_kind": "confirmed-decision", "authority_identity": None,
                    "decision_ids": [decision_id],
                },
            )
            submit["actor_topic_id"] = prepared["target_topic_id"]
            code, claimed, stderr = self.run_cli(submit)
            self.assertEqual(code, 0, stderr)
            code, impact, stderr = self.run_cli(self.handoff_request(
                topic, operation="record-child-result", ledger_revision=current_revision + 1,
                handoff_id=prepared["handoff_id"], child_result_id=claimed["child_result_id"],
                effect="impact",
            ))
            self.assertEqual(code, 0, stderr)
            return impact, current_revision + 2

        unrelated_impact, revision = submit_and_impact(
            unrelated, "codex-thread:unrelated-child", "D-unrelated", revision
        )
        prerequisite_impact, revision = submit_and_impact(
            prerequisite, "codex-thread:prerequisite-child", "D-prerequisite", revision
        )
        before = ledger.read_bytes()
        code, rejected, _ = self.run_cli(self.evolution_request(
            topic, operation="prepare-topic-update", expected_revision=revision,
            expected_topic_revision=1, mutation={
                "type": "resolve-impact", "impact_id": unrelated_impact["impact_id"],
                "action": "accept", "summary": "Unrelated child cannot bypass this gate.",
            },
        ))
        self.assertEqual(code, 1)
        self.assertEqual(rejected["error"]["code"], "topic_gate_closed")
        self.assertEqual(ledger.read_bytes(), before)
        code, accepted, stderr = self.run_cli(self.evolution_request(
            topic, operation="prepare-topic-update", expected_revision=revision,
            expected_topic_revision=1, mutation={
                "type": "resolve-impact", "impact_id": prerequisite_impact["impact_id"],
                "action": "accept", "summary": "The prerequisite child may unblock its gate.",
            },
        ))
        self.assertEqual(code, 0, stderr)
        self.assertEqual(accepted["impact_action"], "accept")

    def test_ticket07_absorb_release_replace_preserves_historical_child_basis_via_cli(self) -> None:
        project = self.make_project("ticket07-absorb-replace-history", git=False)
        topic = self.bootstrap_topic(project)
        prepared, child_ref = self.activate_child_handoff(topic, initial_dependencies=[{"dependent_endpoint": "source", "prerequisite_topic_ref": "target", "requirement_kind": "confirmed-decision", "requirement_summary": "Child decides."}])
        ledger = Path(str(topic["ledger_path"]))
        frontmatter, records = PROTOCOL._load_records(ledger)
        decision = {"decision_id": "D-child", "summary": "Typed.", "rationale": "Current.", "state": "confirmed", "evolution": "confirmed"}
        records["Pending Items"].append({"item_id": "D-child", "item_kind": "decision", "topic_id": prepared["target_topic_id"], "data_json": PROTOCOL._canonical_json(decision)})
        ledger.write_bytes(PROTOCOL._render_records_ledger(frontmatter, records))
        submit = self.handoff_request(topic, operation="submit-child-result", ledger_revision=5, owner_ref=child_ref, handoff_id=prepared["handoff_id"], attempt_id=prepared["attempt_id"], result_scope=["api"], summary="Typed.", authority_selection={"authority_kind": "confirmed-decision", "authority_identity": None, "decision_ids": ["D-child"]})
        submit["actor_topic_id"] = prepared["target_topic_id"]
        code, claimed, stderr = self.run_cli(submit)
        self.assertEqual(code, 0, stderr)
        dependency_id = prepared["initial_dependencies"][0]["dependency_id"]
        release = {"dependency_id": dependency_id, "authority_kind": "confirmed-decision", "authority_identity": None, "decision_ids": ["D-child"]}
        code, _, stderr = self.run_cli(self.handoff_request(topic, operation="record-child-result", ledger_revision=6, handoff_id=prepared["handoff_id"], child_result_id=claimed["child_result_id"], effect="absorb", dependency_releases=[release]))
        self.assertEqual(code, 0, stderr)
        frontmatter, records = PROTOCOL._load_records(ledger)
        second = "topic-" + "e" * 32
        records["Current Topics"].append({"topic_id": second, "record_revision": 1, "root_slug": "second", "parent_topic_id": str(topic["topic_id"]), "current_phase": 1, "phase_state": "active", "review_state": "unreviewed", "topic_state": "open", "topic_document_path": None})
        ledger.write_bytes(PROTOCOL._render_records_ledger(frontmatter, records))
        code, _, stderr = self.run_cli(self.evolution_request(topic, operation="update-topic-dependency", expected_revision=7, expected_topic_revision=1, action="replace", dependency_id=dependency_id, expected_dependency_revision=2, prerequisite_topic_id=second, requirement_kind="phase-1-result", requirement_summary="Second result."))
        self.assertEqual(code, 0, stderr)
        code, read, stderr = self.run_cli(self.evolution_request(topic, operation="read-topic"))
        self.assertEqual(code, 0, stderr)
        dependency = next(item for item in read["topic_dependencies"] if item["dependency_id"] == dependency_id)
        self.assertEqual(dependency["gate_state"], "closed")
        self.assertEqual(json.loads(str(dependency["accepted_basis_json"]))["prerequisite_topic_id"], prepared["target_topic_id"])

    def test_ticket07_initial_dependency_prepare_failure_retries_and_binds_via_cli(self) -> None:
        project = self.make_project("ticket07-initial-dependency-prepare-failure", git=False)
        topic = self.bootstrap_topic(project)
        initial_dependencies = [{
            "dependent_endpoint": "source", "prerequisite_topic_ref": "target",
            "requirement_kind": "confirmed-decision",
            "requirement_summary": "The child selects the API.",
        }]
        request = self.handoff_request(
            topic, operation="prepare-handoff", ledger_revision=1,
            handoff_kind="child", target_slug="api-shape", scope=["api"],
            work_snapshot={"goal": "Choose the API.", "confirmed_decisions": [], "pending_questions": ["Which API?"]},
            authoritative_references=[{"kind": "checkpoint", "identity": "CP-source", "sha256": "1" * 64}],
            initial_dependencies=initial_dependencies,
        )
        ledger = Path(str(topic["ledger_path"]))
        before = ledger.read_bytes()
        code, failed, _ = self.run_cli(
            request, failpoint="handoff-after-initial-dependencies-before-ledger-write"
        )
        self.assertEqual(code, 1)
        self.assertEqual(failed["error"]["code"], "injected_failure")
        self.assertEqual(ledger.read_bytes(), before)
        code, prepared, stderr = self.run_cli(request)
        self.assertEqual(code, 0, stderr)
        self.assertEqual(len(prepared["initial_dependencies"]), 1)
        dependency_id = prepared["initial_dependencies"][0]["dependency_id"]
        self.assertRegex(dependency_id, r"^DEP-[0-9a-f]{32}$")
        code, replay, stderr = self.run_cli(request)
        self.assertEqual(code, 0, stderr)
        self.assertTrue(replay["idempotent_replay"])
        self.assertEqual(replay["initial_dependencies"], prepared["initial_dependencies"])
        bind = self.handoff_request(
            topic, operation="bind-handoff", ledger_revision=2,
            handoff_id=prepared["handoff_id"], attempt_id=prepared["attempt_id"],
            conversation_ref="codex-thread:late-bound-child", verified_identity={
                "project_id": topic["project_id"], "tree_id": topic["tree_id"],
                "topic_id": prepared["target_topic_id"], "handoff_id": prepared["handoff_id"],
                "attempt_id": prepared["attempt_id"], "payload_sha256": prepared["payload_sha256"],
            },
        )
        code, bound, stderr = self.run_cli(bind)
        self.assertEqual(code, 0, stderr)
        code, duplicate_bind, stderr = self.run_cli(bind)
        self.assertEqual(code, 0, stderr)
        self.assertTrue(duplicate_bind["idempotent_replay"])
        self.assertEqual(duplicate_bind["attempt_id"], bound["attempt_id"])
        _, records = PROTOCOL._load_records(ledger)
        self.assertEqual(
            [item["dependency_id"] for item in records["Topic Dependencies"]], [dependency_id]
        )

    def test_ticket07_duplicate_absorb_release_is_atomic_via_cli(self) -> None:
        project = self.make_project("ticket07-duplicate-absorb-release", git=False)
        topic = self.bootstrap_topic(project)
        prepared, child_ref = self.activate_child_handoff(
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
        submit = self.handoff_request(
            topic, operation="submit-child-result", ledger_revision=5,
            owner_ref=child_ref, handoff_id=prepared["handoff_id"],
            attempt_id=prepared["attempt_id"], result_scope=["api"],
            summary="Use typed requests.", authority_selection={
                "authority_kind": "confirmed-decision", "authority_identity": None,
                "decision_ids": ["D-child"],
            },
        )
        submit["actor_topic_id"] = prepared["target_topic_id"]
        code, claimed, stderr = self.run_cli(submit)
        self.assertEqual(code, 0, stderr)
        dependency_id = prepared["initial_dependencies"][0]["dependency_id"]
        release = {
            "dependency_id": dependency_id, "authority_kind": "confirmed-decision",
            "authority_identity": None, "decision_ids": ["D-child"],
        }
        duplicate = self.handoff_request(
            topic, operation="record-child-result", ledger_revision=6,
            handoff_id=prepared["handoff_id"], child_result_id=claimed["child_result_id"],
            effect="absorb", dependency_releases=[release, release],
        )
        before = ledger.read_bytes()
        code, rejected, _ = self.run_cli(duplicate)
        self.assertEqual(code, 1)
        self.assertEqual(rejected["error"]["code"], "invalid_request")
        self.assertEqual(ledger.read_bytes(), before)
        code, handoff, stderr = self.run_cli(self.handoff_request(
            topic, operation="read-handoff", handoff_id=prepared["handoff_id"],
        ))
        self.assertEqual(code, 0, stderr)
        self.assertEqual(handoff["handoff"]["record_revision"], 4)
        self.assertEqual(handoff["attempts"][0]["state"], "active")

    def test_ticket07_injected_absorb_release_is_atomic_via_cli(self) -> None:
        project = self.make_project("ticket07-absorb-release-fault", git=False)
        topic = self.bootstrap_topic(project)
        prepared, child_ref = self.activate_child_handoff(
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
        submit = self.handoff_request(
            topic, operation="submit-child-result", ledger_revision=5, owner_ref=child_ref,
            handoff_id=prepared["handoff_id"], attempt_id=prepared["attempt_id"],
            result_scope=["api"], summary="Use typed requests.", authority_selection={"authority_kind": "confirmed-decision", "authority_identity": None, "decision_ids": ["D-child"]},
        )
        submit["actor_topic_id"] = prepared["target_topic_id"]
        code, claimed, stderr = self.run_cli(submit)
        self.assertEqual(code, 0, stderr)
        release = {"dependency_id": prepared["initial_dependencies"][0]["dependency_id"], "authority_kind": "confirmed-decision", "authority_identity": None, "decision_ids": ["D-child"]}
        absorb = self.handoff_request(
            topic, operation="record-child-result", ledger_revision=6,
            handoff_id=prepared["handoff_id"], child_result_id=claimed["child_result_id"],
            effect="absorb", dependency_releases=[release],
        )
        before = ledger.read_bytes()
        code, failed, _ = self.run_cli(absorb, failpoint="child-result-absorb-before-ledger-write")
        self.assertEqual(code, 1)
        self.assertEqual(failed["error"]["code"], "injected_failure")
        self.assertEqual(ledger.read_bytes(), before)
        code, absorbed, stderr = self.run_cli(absorb)
        self.assertEqual(code, 0, stderr)
        self.assertEqual(absorbed["released_dependency_ids"], [release["dependency_id"]])
        _, records = PROTOCOL._load_records(ledger)
        dependency = next(item for item in records["Topic Dependencies"] if item["dependency_id"] == release["dependency_id"])
        reason = json.loads(str(dependency["gate_reason_json"]))
        self.assertEqual(reason["absorb_operation_id"], absorb["idempotency_key"])
        self.assertEqual(reason["child_result_id"], claimed["child_result_id"])
        code, replay, stderr = self.run_cli(absorb)
        self.assertEqual(code, 0, stderr)
        self.assertTrue(replay["idempotent_replay"])

    def test_ticket07_handoff_crash_recovery_is_cli_idempotent(self) -> None:
        project = self.make_project("ticket07-handoff-crash-recovery", git=False)
        topic = self.bootstrap_topic(project)
        prepared = self.prepare_child_handoff(topic)
        bind = self.handoff_request(
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
        code, failed, _ = self.run_cli(bind, failpoint="handoff-before-binding-ledger-write")
        self.assertEqual(code, 1)
        self.assertEqual(failed["error"]["code"], "injected_failure")
        self.assertEqual(ledger.read_bytes(), before)
        code, bound, stderr = self.run_cli(bind)
        self.assertEqual(code, 0, stderr)
        code, replay, stderr = self.run_cli(bind)
        self.assertEqual(code, 0, stderr)
        self.assertTrue(replay["idempotent_replay"])
        self.assertEqual(replay["attempt_id"], bound["attempt_id"])
