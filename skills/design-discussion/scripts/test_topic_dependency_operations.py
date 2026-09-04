"""Public CLI Ticket-07 dependency update, selection, and invalidation scenarios."""

from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).parent))

import discussion_protocol as PROTOCOL
from test_discussion_protocol import hashlib, json, os, subprocess, uuid, ThreadPoolExecutor
from test_topic_dependency_support import TopicDependencyScenarioTest


class TopicDependencyOperationCliTests(TopicDependencyScenarioTest):
    def test_ticket07_published_authority_blocks_dependency_create_and_replace_via_cli(self) -> None:
        """A published Phase-0/1 authority must be explicitly reopened first."""
        for authority_phase in (0, 1):
            with self.fixture.subTest(authority_phase=authority_phase):
                project = self.fixture.make_project(
                    f"ticket07-published-authority-{authority_phase}", git=False,
                )
                topic = self.fixture.bootstrap_topic(project)
                if authority_phase == 0:
                    self.fixture.publish_non_git_stage_entry_checkpoint(
                        topic, ledger_revision=1,
                    )
                else:
                    _, revision, topic_revision = self.fixture.complete_update(
                        project, topic, ledger_revision=1, topic_revision=1,
                        mutation={"type": "confirm-decision", "summary": "Current.",
                                  "rationale": "Current."},
                    )
                    self.fixture.complete_current_topic_phase(
                        topic, ledger_revision=revision, topic_revision=topic_revision,
                        from_phase=0, to_phase=1,
                    )
                ledger = Path(str(topic["ledger_path"]))
                frontmatter, records = PROTOCOL._load_records(ledger)
                topic_record = next(item for item in records["Current Topics"]
                                    if item["topic_id"] == topic["topic_id"])
                prerequisite_id = "topic-" + uuid.uuid4().hex
                records["Current Topics"].append({
                    "topic_id": prerequisite_id, "record_revision": 1,
                    "root_slug": "prerequisite", "parent_topic_id": topic["topic_id"],
                    "current_phase": 0, "phase_state": "active",
                    "review_state": "unreviewed", "topic_state": "open",
                    "topic_document_path": None,
                })
                ledger.write_bytes(PROTOCOL._render_records_ledger(frontmatter, records))
                revision = int(frontmatter["ledger_revision"])
                before = ledger.read_bytes()
                create = self.fixture.evolution_request(
                    topic, operation="update-topic-dependency", expected_revision=revision,
                    expected_topic_revision=topic_record["record_revision"], action="create",
                    prerequisite_topic_id=prerequisite_id,
                    requirement_kind="confirmed-decision", requirement_summary="A new gate.",
                )
                code, rejected, _ = self.fixture.run_cli(create)
                self.fixture.assertEqual(code, 1)
                self.fixture.assertEqual(rejected["error"]["code"],
                                         "topic_dependency_published_authority_conflict")
                self.fixture.assertEqual(rejected["error"]["context"]["phase"], authority_phase)
                self.fixture.assertEqual(ledger.read_bytes(), before)
                records["Topic Dependencies"].append({
                    "dependency_id": "DEP-" + uuid.uuid4().hex, "record_revision": 1,
                    "dependent_topic_id": topic["topic_id"],
                    "prerequisite_topic_id": prerequisite_id,
                    "requirement_kind": "confirmed-decision",
                    "requirement_summary": "Existing gate.", "relation_state": "active",
                    "gate_state": "closed", "accepted_basis_json": None,
                    "gate_reason_json": PROTOCOL._canonical_json({
                        "kind": "explicit-create",
                        "dependency_update_id": "00000000-0000-4000-8000-000000000000",
                        "ledger_revision": 1,
                    }),
                })
                ledger.write_bytes(PROTOCOL._render_records_ledger(frontmatter, records))
                before = ledger.read_bytes()
                replace = self.fixture.evolution_request(
                    topic, operation="update-topic-dependency", expected_revision=revision,
                    expected_topic_revision=topic_record["record_revision"], action="replace",
                    dependency_id=records["Topic Dependencies"][-1]["dependency_id"],
                    expected_dependency_revision=1, prerequisite_topic_id=prerequisite_id,
                    requirement_kind="phase-1-result", requirement_summary="Replacement.",
                )
                code, rejected, _ = self.fixture.run_cli(replace)
                self.fixture.assertEqual(code, 1)
                self.fixture.assertEqual(rejected["error"]["code"],
                                         "topic_dependency_published_authority_conflict")
                self.fixture.assertEqual(ledger.read_bytes(), before)

    def test_ticket07_nested_authority_selection_is_bounded_and_sorted_via_cli(self) -> None:
        project = self.fixture.make_project("ticket07-nested-authority-bounds", git=False)
        topic = self.fixture.bootstrap_topic(project)
        prepared, child_ref = self.fixture.activate_child_handoff(topic)
        request = self.fixture.handoff_request(
            topic, operation="submit-child-result", ledger_revision=5, owner_ref=child_ref,
            handoff_id=prepared["handoff_id"], attempt_id=prepared["attempt_id"],
            result_scope=["api"], summary="Bounded authority.", authority_selection={
                "authority_kind": "confirmed-decision", "authority_identity": None,
                "decision_ids": [f"D-{index:02d}" for index in range(65)],
            },
        )
        request["actor_topic_id"] = prepared["target_topic_id"]
        ledger = Path(str(topic["ledger_path"]))
        before = ledger.read_bytes()
        code, rejected, _ = self.fixture.run_cli(request)
        self.fixture.assertEqual(code, 1)
        self.fixture.assertEqual(rejected["error"]["code"], "invalid_request")
        self.fixture.assertEqual(ledger.read_bytes(), before)

    def test_ticket07_gate_selection_requires_exact_closed_dependency_set_via_cli(self) -> None:
        project = self.fixture.make_project("ticket07-exact-gate-selection", git=False)
        topic = self.fixture.bootstrap_topic(project)
        prepared = self.fixture.prepare_child_handoff(topic, initial_dependencies=[{
            "dependent_endpoint": "source", "prerequisite_topic_ref": "target",
            "requirement_kind": "confirmed-decision", "requirement_summary": "The child selects the API.",
        }])
        ledger = Path(str(topic["ledger_path"]))
        dependency_id = prepared["initial_dependencies"][0]["dependency_id"]
        before = ledger.read_bytes()
        for selection in (
            [],
            [{"dependency_id": "DEP-" + "f" * 32, "decision_ids": []}],
            [{"dependency_id": dependency_id, "decision_ids": []}] * 2,
        ):
            code, rejected, _ = self.fixture.run_cli(self.fixture.evolution_request(
                topic, operation="evaluate-topic-gate", basis_selection=selection
            ))
            self.fixture.assertEqual(code, 1)
            self.fixture.assertEqual(rejected["error"]["code"], "invalid_request")
            self.fixture.assertEqual(ledger.read_bytes(), before)

    def test_ticket07_active_closed_dependency_limit_is_atomic_via_cli(self) -> None:
        project = self.fixture.make_project("ticket07-active-closed-dependency-limit", git=False)
        topic = self.fixture.bootstrap_topic(project)
        ledger = Path(str(topic["ledger_path"]))
        frontmatter, records = PROTOCOL._load_records(ledger)
        reason = PROTOCOL._canonical_json({"kind": "explicit-create", "dependency_update_id": "00000000-0000-4000-8000-000000000000", "ledger_revision": 1})
        prerequisite_ids = ["topic-" + uuid.uuid4().hex for _ in range(65)]
        records["Current Topics"].extend(
            {"topic_id": topic_id, "record_revision": 1, "root_slug": f"prerequisite-{index}", "parent_topic_id": str(topic["topic_id"]), "current_phase": 0, "phase_state": "active", "review_state": "unreviewed", "topic_state": "open", "topic_document_path": None}
            for index, topic_id in enumerate(prerequisite_ids)
        )
        for index, prerequisite_id in enumerate(prerequisite_ids[:64]):
            decision = {"decision_id": f"D-{index:032x}", "summary": f"Keep prerequisite {index}.", "rationale": "It is an accepted basis.", "state": "confirmed", "evolution": "confirmed"}
            digest = hashlib.sha256(PROTOCOL._canonical_json(decision).encode("utf-8")).hexdigest()
            descriptor = [{"decision_id": decision["decision_id"], "sha256": digest, "summary": decision["summary"]}]
            dependency_id = "DEP-" + uuid.uuid4().hex
            basis = {"basis_version": 1, "dependency_id": dependency_id, "prerequisite_topic_id": prerequisite_id, "requirement_kind": "confirmed-decision", "decision_authority": [{"decision_id": decision["decision_id"], "sha256": digest}], "authority": {"decision_set_digest": hashlib.sha256(PROTOCOL._canonical_json(descriptor).encode("utf-8")).hexdigest()}}
            records["Pending Items"].append({"item_id": decision["decision_id"], "item_kind": "decision", "topic_id": prerequisite_id, "data_json": PROTOCOL._canonical_json(decision)})
            records["Topic Dependencies"].append({"dependency_id": dependency_id, "record_revision": 2, "dependent_topic_id": str(topic["topic_id"]), "prerequisite_topic_id": prerequisite_id, "requirement_kind": "confirmed-decision", "requirement_summary": "A bounded prerequisite.", "relation_state": "active", "gate_state": "open", "accepted_basis_json": PROTOCOL._canonical_json(basis), "gate_reason_json": PROTOCOL._canonical_json({"kind": "atomic-release", "release_id": "00000000-0000-4000-8000-000000000000", "ledger_revision": 1})})
        ledger.write_bytes(PROTOCOL._render_records_ledger(frontmatter, records))
        before = ledger.read_bytes()
        request = self.fixture.evolution_request(topic, operation="update-topic-dependency", expected_revision=1, expected_topic_revision=1, action="create", prerequisite_topic_id=prerequisite_ids[64], requirement_kind="confirmed-decision", requirement_summary="The sixty-fifth prerequisite.")
        code, rejected, _ = self.fixture.run_cli(request)
        self.fixture.assertEqual(code, 1)
        self.fixture.assertEqual(rejected["error"]["code"], "invalid_request")
        self.fixture.assertEqual(ledger.read_bytes(), before)

    def test_ticket07_corrupt_persisted_authorities_fail_closed_via_cli(self) -> None:
        for authority_kind in ("checkpoint", "phase-result"):
            with self.fixture.subTest(authority_kind=authority_kind):
                project = self.fixture.make_project(f"ticket07-corrupt-{authority_kind}", git=False)
                topic = self.fixture.bootstrap_topic(project)
                if authority_kind == "checkpoint":
                    published = self.fixture.publish_non_git_stage_entry_checkpoint(topic, ledger_revision=1)
                    dependency_kind, authority_id, revision = "phase-0-checkpoint", published["checkpoint_id"], 3
                else:
                    decision, revision, topic_revision = self.fixture.complete_update(project, topic, ledger_revision=1, topic_revision=1, mutation={"type": "confirm-decision", "summary": "Current.", "rationale": "Current."})
                    completed, revision, _ = self.fixture.complete_current_topic_phase(topic, ledger_revision=revision, topic_revision=topic_revision, from_phase=0, to_phase=1)
                    dependency_kind, authority_id = "phase-1-result", completed["phase_result_id"]
                ledger = Path(str(topic["ledger_path"]))
                frontmatter, records = PROTOCOL._load_records(ledger)
                dependent_id, dependency_id = "topic-" + uuid.uuid4().hex, "DEP-" + uuid.uuid4().hex
                records["Current Topics"].append({"topic_id": dependent_id, "record_revision": 1, "root_slug": "dependent", "parent_topic_id": str(topic["topic_id"]), "current_phase": 0, "phase_state": "active", "review_state": "unreviewed", "topic_state": "open", "topic_document_path": None})
                records["Conversation Bindings"].append({"topic_id": dependent_id, "conversation_ref": "codex-thread:dependent", "binding_state": "active", "record_revision": 1, "handoff_id": None, "attempt_id": None})
                records["Topic Dependencies"].append({"dependency_id": dependency_id, "record_revision": 1, "dependent_topic_id": dependent_id, "prerequisite_topic_id": str(topic["topic_id"]), "requirement_kind": dependency_kind, "requirement_summary": "Authority remains exact.", "relation_state": "active", "gate_state": "closed", "accepted_basis_json": None, "gate_reason_json": PROTOCOL._canonical_json({"kind": "explicit-create", "dependency_update_id": "00000000-0000-4000-8000-000000000000", "ledger_revision": 1})})
                target = records["Checkpoints"][0] if authority_kind == "checkpoint" else next(item for item in records["Phase Results"] if item["result_id"] == authority_id)
                target["data_json"] = "not-canonical-json"
                ledger.write_bytes(PROTOCOL._render_records_ledger(frontmatter, records))
                before = ledger.read_bytes()
                request = self.fixture.evolution_request(topic, operation="evaluate-topic-gate", basis_selection=[{"dependency_id": dependency_id, "authority_id": authority_id, "decision_ids": []}])
                request["actor_topic_id"] = dependent_id
                request["actor_conversation_ref"] = "codex-thread:dependent"
                code, rejected, _ = self.fixture.run_cli(request)
                self.fixture.assertEqual(code, 1)
                self.fixture.assertEqual(rejected["error"]["code"], "state_corrupt")
                self.fixture.assertEqual(ledger.read_bytes(), before)

    def test_ticket07_forged_child_basis_authority_mismatch_fails_closed_via_cli(self) -> None:
        project = self.fixture.make_project("ticket07-forged-child-basis", git=False)
        topic = self.fixture.bootstrap_topic(project)
        prepared, child_ref = self.fixture.activate_child_handoff(topic, initial_dependencies=[{
            "dependent_endpoint": "source", "prerequisite_topic_ref": "target",
            "requirement_kind": "confirmed-decision", "requirement_summary": "The child selects the API.",
        }])
        ledger = Path(str(topic["ledger_path"]))
        frontmatter, records = PROTOCOL._load_records(ledger)
        decision = {"decision_id": "D-child", "summary": "Current.", "rationale": "Current.", "state": "confirmed", "evolution": "confirmed"}
        records["Pending Items"].append({"item_id": "D-child", "item_kind": "decision", "topic_id": prepared["target_topic_id"], "data_json": PROTOCOL._canonical_json(decision)})
        ledger.write_bytes(PROTOCOL._render_records_ledger(frontmatter, records))
        submit = self.fixture.handoff_request(topic, operation="submit-child-result", ledger_revision=5, owner_ref=child_ref, handoff_id=prepared["handoff_id"], attempt_id=prepared["attempt_id"], result_scope=["api"], summary="Current.", authority_selection={"authority_kind": "confirmed-decision", "authority_identity": None, "decision_ids": ["D-child"]})
        submit["actor_topic_id"] = prepared["target_topic_id"]
        code, claimed, stderr = self.fixture.run_cli(submit)
        self.fixture.assertEqual(code, 0, stderr)
        self.fixture.assertEqual(claimed["frozen_authority"]["decision_ids"], ["D-child"])
        release = {"dependency_id": prepared["initial_dependencies"][0]["dependency_id"], "authority_kind": "confirmed-decision", "authority_identity": None, "decision_ids": ["D-child"]}
        absorb = self.fixture.handoff_request(topic, operation="record-child-result", ledger_revision=6, handoff_id=prepared["handoff_id"], child_result_id=claimed["child_result_id"], effect="absorb", dependency_releases=[release])
        code, absorbed, stderr = self.fixture.run_cli(absorb)
        self.fixture.assertEqual(code, 0, stderr)
        self.fixture.assertEqual(absorbed["frozen_authority"], claimed["frozen_authority"])
        frontmatter, records = PROTOCOL._load_records(ledger)
        child = next(item for item in records["Phase Results"] if item["result_id"] == claimed["child_result_id"])
        frozen = json.loads(str(child["authority_json"]))
        frozen["decision_ids"] = ["D-forged"]
        child["authority_json"] = PROTOCOL._canonical_json(frozen)
        ledger.write_bytes(PROTOCOL._render_records_ledger(frontmatter, records))
        before = ledger.read_bytes()
        code, rejected, _ = self.fixture.run_cli(self.fixture.evolution_request(topic, operation="read-topic"))
        self.fixture.assertEqual(code, 1)
        self.fixture.assertEqual(rejected["error"]["code"], "state_corrupt")
        self.fixture.assertEqual(ledger.read_bytes(), before)

    def test_ticket07_concurrent_dependency_updates_commit_once_via_cli(self) -> None:
        project = self.fixture.make_project("ticket07-concurrent-dependency-updates", git=False)
        topic = self.fixture.bootstrap_topic(project)
        child = self.fixture.prepare_child_handoff(topic)
        code, created, stderr = self.fixture.run_cli(self.fixture.evolution_request(
            topic, operation="update-topic-dependency", expected_revision=2,
            expected_topic_revision=1, action="create",
            prerequisite_topic_id=child["target_topic_id"],
            requirement_kind="confirmed-decision", requirement_summary="The child selects the API.",
        ))
        self.fixture.assertEqual(code, 0, stderr)
        replace = self.fixture.evolution_request(
            topic, operation="update-topic-dependency", expected_revision=3,
            expected_topic_revision=1, action="replace",
            dependency_id=created["dependency_id"], expected_dependency_revision=1,
            prerequisite_topic_id=child["target_topic_id"],
            requirement_kind="phase-1-result", requirement_summary="The child completes Phase 1.",
        )
        cancel = self.fixture.evolution_request(
            topic, operation="update-topic-dependency", expected_revision=3,
            expected_topic_revision=1, action="cancel",
            dependency_id=created["dependency_id"], expected_dependency_revision=1,
        )

        def invoke(request: dict[str, object]) -> tuple[int, dict[str, object], str]:
            return self.fixture.run_cli(request)

        with ThreadPoolExecutor(max_workers=2) as executor:
            outcomes = list(executor.map(invoke, (replace, cancel)))
        successful = [response for code, response, _ in outcomes if code == 0]
        conflicted = [response for code, response, _ in outcomes if code == 1]
        self.fixture.assertEqual(len(successful), 1)
        self.fixture.assertEqual(len(conflicted), 1)
        self.fixture.assertEqual(conflicted[0]["error"]["code"], "ledger_revision_conflict")
        ledger = Path(str(topic["ledger_path"]))
        _, records = PROTOCOL._load_records(ledger)
        dependency = next(item for item in records["Topic Dependencies"] if item["dependency_id"] == created["dependency_id"])
        self.fixture.assertEqual(int(PROTOCOL._load_records(ledger)[0]["ledger_revision"]), 4)
        self.fixture.assertEqual(dependency["record_revision"], 2)
        self.fixture.assertIn(dependency["relation_state"], {"active", "cancelled"})
        self.fixture.assertEqual(len([event for event in records["Recent Events"] if event["event_type"].startswith("topic-dependency-")]), 2)

    def test_ticket07_injected_dependency_update_is_atomic_via_cli(self) -> None:
        for action in ("create", "replace", "cancel"):
            with self.fixture.subTest(action=action):
                project = self.fixture.make_project(f"ticket07-dependency-{action}-fault", git=False)
                topic = self.fixture.bootstrap_topic(project)
                child = self.fixture.prepare_child_handoff(topic)
                created = None
                if action != "create":
                    code, created, stderr = self.fixture.run_cli(self.fixture.evolution_request(
                        topic, operation="update-topic-dependency", expected_revision=2,
                        expected_topic_revision=1, action="create",
                        prerequisite_topic_id=child["target_topic_id"],
                        requirement_kind="confirmed-decision", requirement_summary="The child selects the API.",
                    ))
                    self.fixture.assertEqual(code, 0, stderr)
                request = self.fixture.evolution_request(
                    topic, operation="update-topic-dependency",
                    expected_revision=2 if action == "create" else 3,
                    expected_topic_revision=1, action=action,
                    **({"prerequisite_topic_id": child["target_topic_id"], "requirement_kind": "confirmed-decision", "requirement_summary": "The child selects the API."} if action == "create" else {"dependency_id": created["dependency_id"], "expected_dependency_revision": 1, **({"prerequisite_topic_id": child["target_topic_id"], "requirement_kind": "phase-1-result", "requirement_summary": "The child completes Phase 1."} if action == "replace" else {})}),
                )
                ledger = Path(str(topic["ledger_path"]))
                before = ledger.read_bytes()
                code, failed, _ = self.fixture.run_cli(request, failpoint="topic-dependency-before-ledger-write")
                self.fixture.assertEqual(code, 1)
                self.fixture.assertEqual(failed["error"]["code"], "injected_failure")
                self.fixture.assertEqual(ledger.read_bytes(), before)
                code, completed, stderr = self.fixture.run_cli(request)
                self.fixture.assertEqual(code, 0, stderr)
                code, replay, stderr = self.fixture.run_cli(request)
                self.fixture.assertEqual(code, 0, stderr)
                self.fixture.assertTrue(replay["idempotent_replay"])
                self.fixture.assertEqual(replay["dependency_id"], completed["dependency_id"])

    def test_ticket07_dependency_update_reasons_keep_exact_operation_ids_via_cli(self) -> None:
        project = self.fixture.make_project("ticket07-dependency-operation-identities", git=False)
        topic = self.fixture.bootstrap_topic(project)
        child = self.fixture.prepare_child_handoff(topic)
        ledger = Path(str(topic["ledger_path"]))
        create = self.fixture.evolution_request(topic, operation="update-topic-dependency", expected_revision=2, expected_topic_revision=1, action="create", prerequisite_topic_id=child["target_topic_id"], requirement_kind="confirmed-decision", requirement_summary="The child selects the API.")
        code, created, stderr = self.fixture.run_cli(create)
        self.fixture.assertEqual(code, 0, stderr)
        replace = self.fixture.evolution_request(topic, operation="update-topic-dependency", expected_revision=3, expected_topic_revision=1, action="replace", dependency_id=created["dependency_id"], expected_dependency_revision=1, prerequisite_topic_id=child["target_topic_id"], requirement_kind="phase-1-result", requirement_summary="The child completes Phase 1.")
        code, _, stderr = self.fixture.run_cli(replace)
        self.fixture.assertEqual(code, 0, stderr)
        cancel = self.fixture.evolution_request(topic, operation="update-topic-dependency", expected_revision=4, expected_topic_revision=1, action="cancel", dependency_id=created["dependency_id"], expected_dependency_revision=2)
        code, _, stderr = self.fixture.run_cli(cancel)
        self.fixture.assertEqual(code, 0, stderr)
        _, records = PROTOCOL._load_records(ledger)
        dependency = next(item for item in records["Topic Dependencies"] if item["dependency_id"] == created["dependency_id"])
        reason = json.loads(str(dependency["gate_reason_json"]))
        self.fixture.assertEqual(reason["kind"], "explicit-cancel")
        self.fixture.assertEqual(reason["dependency_update_id"], cancel["idempotency_key"])
        self.fixture.assertNotEqual(reason["dependency_update_id"], str(dependency["record_revision"]))
        before = ledger.read_bytes()
        code, replay, stderr = self.fixture.run_cli(cancel)
        self.fixture.assertEqual(code, 0, stderr)
        self.fixture.assertTrue(replay["idempotent_replay"])
        self.fixture.assertEqual(ledger.read_bytes(), before)

    def test_ticket07_direct_release_reason_keeps_exact_operation_id_via_cli(self) -> None:
        project = self.fixture.make_project("ticket07-direct-release-identity", git=False)
        topic = self.fixture.bootstrap_topic(project)
        child = self.fixture.prepare_child_handoff(topic, initial_dependencies=[{"dependent_endpoint": "source", "prerequisite_topic_ref": "target", "requirement_kind": "confirmed-decision", "requirement_summary": "The child selects the API."}])
        ledger = Path(str(topic["ledger_path"]))
        frontmatter, records = PROTOCOL._load_records(ledger)
        decision = {"decision_id": "D-child", "summary": "Use typed requests.", "rationale": "Current.", "state": "confirmed", "evolution": "confirmed"}
        records["Pending Items"].append({"item_id": "D-child", "item_kind": "decision", "topic_id": child["target_topic_id"], "data_json": PROTOCOL._canonical_json(decision)})
        ledger.write_bytes(PROTOCOL._render_records_ledger(frontmatter, records))
        dependency_id = child["initial_dependencies"][0]["dependency_id"]
        code, evaluation, stderr = self.fixture.run_cli(self.fixture.evolution_request(topic, operation="evaluate-topic-gate", basis_selection=[{"dependency_id": dependency_id, "decision_ids": ["D-child"]}]))
        self.fixture.assertEqual(code, 0, stderr)
        release = self.fixture.evolution_request(topic, operation="release-topic-gate", expected_revision=2, expected_topic_revision=1, release_set=evaluation["release_set"], release_set_sha256=evaluation["release_set_sha256"])
        code, _, stderr = self.fixture.run_cli(release)
        self.fixture.assertEqual(code, 0, stderr)
        _, records = PROTOCOL._load_records(ledger)
        dependency = next(item for item in records["Topic Dependencies"] if item["dependency_id"] == dependency_id)
        self.fixture.assertEqual(json.loads(str(dependency["gate_reason_json"]))["release_id"], release["idempotency_key"])
        before = ledger.read_bytes()
        code, replay, stderr = self.fixture.run_cli(release)
        self.fixture.assertEqual(code, 0, stderr)
        self.fixture.assertTrue(replay["idempotent_replay"])
        self.fixture.assertEqual(ledger.read_bytes(), before)

    def test_ticket07_reopen_reason_keeps_exact_result_and_operation_ids_via_cli(self) -> None:
        project = self.fixture.make_project("ticket07-reopen-operation-identity", git=False)
        topic = self.fixture.bootstrap_topic(project)
        decision, ledger_revision, topic_revision = self.fixture.complete_update(project, topic, ledger_revision=1, topic_revision=1, mutation={"type": "confirm-decision", "summary": "Keep the API.", "rationale": "Phase 1 depends on it."})
        completed, ledger_revision, topic_revision = self.fixture.complete_current_topic_phase(topic, ledger_revision=ledger_revision, topic_revision=topic_revision, from_phase=0, to_phase=1)
        ledger = Path(str(topic["ledger_path"]))
        b_id = "topic-" + "e" * 32
        frontmatter, records = PROTOCOL._load_records(ledger)
        records["Current Topics"].append({"topic_id": b_id, "record_revision": 1, "root_slug": "dependent", "parent_topic_id": str(topic["topic_id"]), "current_phase": 1, "phase_state": "active", "review_state": "unreviewed", "topic_state": "open", "topic_document_path": None})
        records["Conversation Bindings"].append({"topic_id": b_id, "conversation_ref": "codex-thread:dependent", "binding_state": "active", "record_revision": 1, "handoff_id": None, "attempt_id": None})
        dependency_id = "DEP-" + "d" * 32
        records["Topic Dependencies"].append({"dependency_id": dependency_id, "record_revision": 1, "dependent_topic_id": b_id, "prerequisite_topic_id": str(topic["topic_id"]), "requirement_kind": "phase-1-result", "requirement_summary": "The Phase 1 result remains current.", "relation_state": "active", "gate_state": "closed", "accepted_basis_json": None, "gate_reason_json": PROTOCOL._canonical_json({"kind": "explicit-create", "dependency_update_id": "00000000-0000-4000-8000-000000000000", "ledger_revision": 1})})
        ledger.write_bytes(PROTOCOL._render_records_ledger(frontmatter, records))
        code, evaluation, stderr = self.fixture.run_cli({**self.fixture.evolution_request(topic, operation="evaluate-topic-gate", basis_selection=[{"dependency_id": dependency_id, "authority_id": completed["phase_result_id"], "decision_ids": [decision["decision_id"]]}]), "actor_topic_id": b_id, "actor_conversation_ref": "codex-thread:dependent"})
        self.fixture.assertEqual(code, 0, stderr)
        code, _, stderr = self.fixture.run_cli({**self.fixture.evolution_request(topic, operation="release-topic-gate", expected_revision=ledger_revision, expected_topic_revision=1, release_set=evaluation["release_set"], release_set_sha256=evaluation["release_set_sha256"]), "actor_topic_id": b_id, "actor_conversation_ref": "codex-thread:dependent"})
        self.fixture.assertEqual(code, 0, stderr)
        reopen = self.fixture.phase_request(topic, "reopen-phase", ledger_revision + 1, topic_revision=topic_revision, affected_decision_ids=[decision["decision_id"]], review={decision["decision_id"]: "adjust"}, reason="The API changed.")
        code, reopened, stderr = self.fixture.run_cli(reopen)
        self.fixture.assertEqual(code, 0, stderr)
        _, records = PROTOCOL._load_records(ledger)
        dependency = next(item for item in records["Topic Dependencies"] if item["dependency_id"] == dependency_id)
        reason = json.loads(str(dependency["gate_reason_json"]))
        self.fixture.assertEqual(reason["reopen_id"], reopen["idempotency_key"])
        self.fixture.assertEqual(reason["invalidated_result_ids"], [completed["phase_result_id"]])
        self.fixture.assertEqual(reason["affected_decision_ids"], [decision["decision_id"]])
        before = ledger.read_bytes()
        code, replay, stderr = self.fixture.run_cli(reopen)
        self.fixture.assertEqual(code, 0, stderr)
        self.fixture.assertTrue(replay["idempotent_replay"])
        self.fixture.assertEqual(ledger.read_bytes(), before)

    def test_ticket07_reopen_all_keep_recloses_result_gate_via_cli(self) -> None:
        project = self.fixture.make_project("ticket07-reopen-all-keep", git=False)
        topic = self.fixture.bootstrap_topic(project)
        decision, revision, topic_revision = self.fixture.complete_update(project, topic, ledger_revision=1, topic_revision=1, mutation={"type": "confirm-decision", "summary": "Keep the API.", "rationale": "Phase 1 depends on it."})
        completed, revision, topic_revision = self.fixture.complete_current_topic_phase(topic, ledger_revision=revision, topic_revision=topic_revision, from_phase=0, to_phase=1)
        ledger = Path(str(topic["ledger_path"]))
        b_id = "topic-" + "f" * 32
        frontmatter, records = PROTOCOL._load_records(ledger)
        records["Current Topics"].append({"topic_id": b_id, "record_revision": 1, "root_slug": "dependent", "parent_topic_id": str(topic["topic_id"]), "current_phase": 1, "phase_state": "active", "review_state": "unreviewed", "topic_state": "open", "topic_document_path": None})
        records["Conversation Bindings"].append({"topic_id": b_id, "conversation_ref": "codex-thread:dependent", "binding_state": "active", "record_revision": 1, "handoff_id": None, "attempt_id": None})
        dependency_id = "DEP-" + "e" * 32
        records["Topic Dependencies"].append({"dependency_id": dependency_id, "record_revision": 1, "dependent_topic_id": b_id, "prerequisite_topic_id": str(topic["topic_id"]), "requirement_kind": "phase-1-result", "requirement_summary": "The Phase 1 result remains current.", "relation_state": "active", "gate_state": "closed", "accepted_basis_json": None, "gate_reason_json": PROTOCOL._canonical_json({"kind": "explicit-create", "dependency_update_id": "00000000-0000-4000-8000-000000000000", "ledger_revision": 1})})
        ledger.write_bytes(PROTOCOL._render_records_ledger(frontmatter, records))
        code, evaluation, stderr = self.fixture.run_cli({**self.fixture.evolution_request(topic, operation="evaluate-topic-gate", basis_selection=[{"dependency_id": dependency_id, "authority_id": completed["phase_result_id"], "decision_ids": [decision["decision_id"]]}]), "actor_topic_id": b_id, "actor_conversation_ref": "codex-thread:dependent"})
        self.fixture.assertEqual(code, 0, stderr)
        code, _, stderr = self.fixture.run_cli({**self.fixture.evolution_request(topic, operation="release-topic-gate", expected_revision=revision, expected_topic_revision=1, release_set=evaluation["release_set"], release_set_sha256=evaluation["release_set_sha256"]), "actor_topic_id": b_id, "actor_conversation_ref": "codex-thread:dependent"})
        self.fixture.assertEqual(code, 0, stderr)
        reopen = self.fixture.phase_request(topic, "reopen-phase", revision + 1, topic_revision=topic_revision, affected_decision_ids=[decision["decision_id"]], review={decision["decision_id"]: "keep"}, reason="Re-evaluate the completed result.")
        code, _, stderr = self.fixture.run_cli(reopen)
        self.fixture.assertEqual(code, 0, stderr)
        _, records = PROTOCOL._load_records(ledger)
        dependency = next(item for item in records["Topic Dependencies"] if item["dependency_id"] == dependency_id)
        reason = json.loads(str(dependency["gate_reason_json"]))
        self.fixture.assertEqual(dependency["gate_state"], "closed")
        self.fixture.assertEqual(reason["reopen_id"], reopen["idempotency_key"])
        self.fixture.assertEqual(reason["invalidated_result_ids"], [completed["phase_result_id"]])
        self.fixture.assertEqual(reason["affected_decision_ids"], [])
        self.fixture.assertEqual(next(item for item in records["Current Topics"] if item["topic_id"] == b_id)["phase_state"], "active")
        before = ledger.read_bytes()
        code, replay, stderr = self.fixture.run_cli(reopen)
        self.fixture.assertEqual(code, 0, stderr)
        self.fixture.assertTrue(replay["idempotent_replay"])
        self.fixture.assertEqual(ledger.read_bytes(), before)
