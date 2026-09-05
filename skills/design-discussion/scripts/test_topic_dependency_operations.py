"""Public CLI Ticket-07 dependency update, selection, and invalidation scenarios."""

from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).parent))

import discussion_protocol as PROTOCOL
from test_discussion_protocol import hashlib, json, os, subprocess, uuid, ThreadPoolExecutor
from test_topic_dependency_support import DiscussionProtocolScenarioFixture, DiscussionProtocolTestSupport, add_topic


class TopicDependencyOperationCliTests(DiscussionProtocolScenarioFixture, DiscussionProtocolTestSupport):
    def test_ticket07_phase_two_reopen_recloses_stale_own_gate_via_cli(self) -> None:
        project = self.make_project("ticket07-reopen-stale-own-gate", git=False)
        topic = self.bootstrap_topic(project)
        root_decision, revision, topic_revision = self.complete_update(
            project, topic, ledger_revision=1, topic_revision=1, mutation={
                "type": "confirm-decision", "summary": "Keep the root API.",
                "rationale": "The reopen review is authoritative.",
            },
        )
        child = self.prepare_child_handoff(
            topic, ledger_revision=revision, topic_revision=topic_revision,
            initial_dependencies=[{
                "dependent_endpoint": "source", "prerequisite_topic_ref": "target",
                "requirement_kind": "confirmed-decision",
                "requirement_summary": "The child decision is required before Phase 2.",
            }],
        )
        revision += 1
        ledger = Path(str(topic["ledger_path"]))
        frontmatter, records = PROTOCOL._load_records(ledger)
        child_decision = {
            "decision_id": "D-child", "summary": "Use typed requests.",
            "rationale": "Initially authoritative.", "state": "confirmed",
            "evolution": "confirmed",
        }
        records["Pending Items"].append({
            "item_id": "D-child", "item_kind": "decision",
            "topic_id": child["target_topic_id"],
            "data_json": PROTOCOL._canonical_json(child_decision),
        })
        ledger.write_bytes(PROTOCOL._render_records_ledger(frontmatter, records))
        dependency_id = child["initial_dependencies"][0]["dependency_id"]
        code, evaluation, stderr = self.run_cli(self.evolution_request(
            topic, operation="evaluate-topic-gate", basis_selection=[{
                "dependency_id": dependency_id, "decision_ids": ["D-child"],
            }],
        ))
        self.assertEqual(code, 0, stderr)
        code, _, stderr = self.run_cli(self.evolution_request(
            topic, operation="release-topic-gate", expected_revision=revision,
            expected_topic_revision=topic_revision, release_set=evaluation["release_set"],
            release_set_sha256=evaluation["release_set_sha256"],
        ))
        self.assertEqual(code, 0, stderr)
        revision += 1
        _, revision, topic_revision = self.complete_current_topic_phase(
            topic, ledger_revision=revision, topic_revision=topic_revision,
            from_phase=0, to_phase=1,
        )
        _, revision, topic_revision = self.complete_current_topic_phase(
            topic, ledger_revision=revision, topic_revision=topic_revision,
            from_phase=1, to_phase=2,
        )
        frontmatter, records = PROTOCOL._load_records(ledger)
        child_record = next(item for item in records["Pending Items"] if item["item_id"] == "D-child")
        changed_child = json.loads(child_record["data_json"])
        changed_child["summary"] = "Use a revised typed request model."
        changed_child["evolution"] = "adjusted upstream while dependent was in Phase 2"
        child_record["data_json"] = PROTOCOL._canonical_json(changed_child)
        ledger.write_bytes(PROTOCOL._render_records_ledger(frontmatter, records))
        before_reopen = ledger.read_bytes()
        invalid = self.phase_request(
            topic, "reopen-phase", revision, topic_revision=topic_revision,
            affected_decision_ids=[], review={},
            reason="An incomplete review cannot change the reopened gate.",
        )
        code, rejected, _ = self.run_cli(invalid)
        self.assertEqual(code, 1)
        self.assertEqual(rejected["error"]["code"], "phase_reopen_review_required")
        self.assertEqual(ledger.read_bytes(), before_reopen)
        reopen = self.phase_request(
            topic, "reopen-phase", revision, topic_revision=topic_revision,
            affected_decision_ids=[root_decision["decision_id"]],
            review={root_decision["decision_id"]: "keep"},
            reason="The Phase 2 topic must recheck its prerequisite on reopen.",
        )
        code, reopened, stderr = self.run_cli(reopen)
        self.assertEqual(code, 0, stderr)
        self.assertEqual(reopened["reclosed_dependency_ids"], [dependency_id])
        code, current, stderr = self.run_cli(self.evolution_request(topic, operation="read-topic"))
        self.assertEqual(code, 0, stderr)
        dependency = next(item for item in current["topic_dependencies"]
                          if item["dependency_id"] == dependency_id)
        self.assertEqual(dependency["gate_state"], "closed")

    def test_ticket07_keep_impact_preserves_open_authority_basis_via_cli(self) -> None:
        project = self.make_project("ticket07-keep-impact-authority", git=False)
        topic = self.bootstrap_topic(project)
        handoff = self.prepare_child_handoff(topic, initial_dependencies=[{
            "dependent_endpoint": "source", "prerequisite_topic_ref": "target",
            "requirement_kind": "confirmed-decision",
            "requirement_summary": "The child decision must remain authoritative.",
        }])
        ledger = Path(str(topic["ledger_path"]))
        frontmatter, records = PROTOCOL._load_records(ledger)
        child_decision = {
            "decision_id": "D-child", "summary": "Use typed requests.",
            "rationale": "It gates the parent.", "state": "confirmed",
            "evolution": "confirmed",
        }
        root_decision = {
            "decision_id": "D-root", "summary": "Keep the deployment model.",
            "rationale": "The impact only records review.", "state": "confirmed",
            "evolution": "confirmed",
        }
        records["Pending Items"].extend([
            {"item_id": "D-child", "item_kind": "decision",
             "topic_id": handoff["target_topic_id"],
             "data_json": PROTOCOL._canonical_json(child_decision)},
            {"item_id": "D-root", "item_kind": "decision",
             "topic_id": topic["topic_id"],
             "data_json": PROTOCOL._canonical_json(root_decision)},
        ])
        impact_id = "IMP-" + uuid.uuid4().hex
        records["Impacts"].append({
            "impact_id": impact_id, "topic_id": topic["topic_id"],
            "data_json": PROTOCOL._canonical_json({
                "impact_id": impact_id, "decision_id": "D-root",
                "direction": "Reconsider deployment.", "state": "pending", "action": None,
            }),
        })
        ledger.write_bytes(PROTOCOL._render_records_ledger(frontmatter, records))
        dependency_id = handoff["initial_dependencies"][0]["dependency_id"]
        code, evaluation, stderr = self.run_cli(self.evolution_request(
            topic, operation="evaluate-topic-gate", basis_selection=[{
                "dependency_id": dependency_id, "decision_ids": ["D-child"],
            }],
        ))
        self.assertEqual(code, 0, stderr)
        code, _, stderr = self.run_cli(self.evolution_request(
            topic, operation="release-topic-gate", expected_revision=2,
            expected_topic_revision=1, release_set=evaluation["release_set"],
            release_set_sha256=evaluation["release_set_sha256"],
        ))
        self.assertEqual(code, 0, stderr)
        _, before_records = PROTOCOL._load_records(ledger)
        basis_before = next(item["accepted_basis_json"] for item in before_records["Topic Dependencies"]
                            if item["dependency_id"] == dependency_id)
        decision_before = next(item["data_json"] for item in before_records["Pending Items"]
                               if item["item_id"] == "D-root")
        keep = self.evolution_request(
            topic, operation="prepare-topic-update", expected_revision=3,
            expected_topic_revision=1, mutation={
                "type": "resolve-impact", "impact_id": impact_id,
                "decision_id": "D-root", "action": "keep",
                "summary": "Keep records the review without changing authority.",
            },
        )
        code, prepared, stderr = self.run_cli(keep)
        self.assertEqual(code, 0, stderr)
        code, _, stderr = self.run_cli(self.evolution_request(
            topic, operation="apply-document-write", expected_revision=4,
            expected_topic_revision=2, document_write_id=prepared["document_write_id"],
        ))
        self.assertEqual(code, 0, stderr)
        _, after_records = PROTOCOL._load_records(ledger)
        self.assertEqual(
            next(item["accepted_basis_json"] for item in after_records["Topic Dependencies"]
                 if item["dependency_id"] == dependency_id), basis_before,
        )
        self.assertEqual(
            next(item["data_json"] for item in after_records["Pending Items"]
                 if item["item_id"] == "D-root"), decision_before,
        )
        code, current, stderr = self.run_cli(self.evolution_request(topic, operation="read-topic"))
        self.assertEqual(code, 0, stderr)
        self.assertEqual(current["derived_gate_state"], "open")

    def test_ticket07_closed_gate_blocks_decision_impact_resolution_via_cli(self) -> None:
        for action in ("adjust", "replace", "discard"):
            with self.subTest(action=action):
                project = self.make_project(
                    f"ticket07-closed-impact-{action}", git=False,
                )
                topic = self.bootstrap_topic(project)
                self.prepare_child_handoff(topic, initial_dependencies=[{
                    "dependent_endpoint": "source", "prerequisite_topic_ref": "target",
                    "requirement_kind": "confirmed-decision",
                    "requirement_summary": "The child authority is required first.",
                }])
                ledger = Path(str(topic["ledger_path"]))
                frontmatter, records = PROTOCOL._load_records(ledger)
                decision_id = "D-impact"
                records["Pending Items"].append({
                    "item_id": decision_id, "item_kind": "decision",
                    "topic_id": topic["topic_id"],
                    "data_json": PROTOCOL._canonical_json({
                        "decision_id": decision_id, "summary": "Original.",
                        "rationale": "Still under review.", "state": "confirmed",
                        "evolution": "confirmed",
                    }),
                })
                impact_id = "IMP-" + uuid.uuid4().hex
                records["Impacts"].append({
                    "impact_id": impact_id, "topic_id": topic["topic_id"],
                    "data_json": PROTOCOL._canonical_json({
                        "impact_id": impact_id, "decision_id": decision_id,
                        "direction": "Change the decision.", "state": "pending",
                        "action": None,
                    }),
                })
                ledger.write_bytes(PROTOCOL._render_records_ledger(frontmatter, records))
                before = ledger.read_bytes()
                request = self.evolution_request(
                    topic, operation="prepare-topic-update", expected_revision=2,
                    expected_topic_revision=1, mutation={
                        "type": "resolve-impact", "impact_id": impact_id,
                        "decision_id": decision_id, "action": action,
                        "summary": f"{action} requires the gate to be open.",
                    },
                )
                code, rejected, _ = self.run_cli(request)
                self.assertEqual(code, 1)
                self.assertEqual(rejected["error"]["code"], "topic_gate_closed")
                self.assertEqual(ledger.read_bytes(), before)

    def test_ticket07_source_initial_dependency_rejects_published_authority_via_cli(self) -> None:
        for phase in (0, 1):
            with self.subTest(phase=phase):
                project = self.make_project(
                    f"ticket07-source-initial-published-{phase}", git=False,
                )
                topic = self.bootstrap_topic(project)
                ledger_revision = 1
                topic_revision = 1
                if phase == 1:
                    _, ledger_revision, topic_revision = self.complete_current_topic_phase(
                        topic, ledger_revision=ledger_revision,
                        topic_revision=topic_revision, from_phase=0, to_phase=1,
                    )
                self.publish_non_git_stage_entry_checkpoint(
                    topic, ledger_revision=ledger_revision, topic_revision=topic_revision,
                )
                ledger_revision += 2
                ledger = Path(str(topic["ledger_path"]))
                before = ledger.read_bytes()
                request = self.handoff_request(
                    topic, operation="prepare-handoff", ledger_revision=ledger_revision,
                    topic_revision=topic_revision, handoff_kind="child",
                    target_slug="published-source", scope=["api"],
                    work_snapshot={
                        "goal": "Delegate dependent work.",
                        "confirmed_decisions": [],
                        "pending_questions": ["Which authority remains current?"],
                    },
                    authoritative_references=[{
                        "kind": "checkpoint", "identity": "CP-source", "sha256": "1" * 64,
                    }],
                    initial_dependencies=[{
                        "dependent_endpoint": "source", "prerequisite_topic_ref": "target",
                        "requirement_kind": "confirmed-decision",
                        "requirement_summary": "The child must provide the decision.",
                    }],
                )
                code, rejected, _ = self.run_cli(request)
                self.assertEqual(code, 1)
                self.assertEqual(
                    rejected["error"]["code"],
                    "topic_dependency_published_authority_conflict",
                )
                self.assertEqual(rejected["error"]["context"]["phase"], phase)
                self.assertEqual(ledger.read_bytes(), before)

    def test_ticket07_revision_booleans_are_rejected_in_request_and_ledger_via_cli(self) -> None:
        project = self.make_project("ticket07-exact-integers", git=False)
        topic = self.bootstrap_topic(project)
        ledger = Path(str(topic["ledger_path"]))
        before = ledger.read_bytes()
        for field in ("expected_ledger_revision", "expected_topic_revision"):
            with self.subTest(request_field=field):
                request = self.evolution_request(
                    topic, operation="prepare-topic-update", mutation={
                        "type": "confirm-decision", "summary": "Typed revision.",
                        "rationale": "Boolean revisions are invalid.",
                    }, expected_revision=1, expected_topic_revision=1,
                )
                request[field] = True
                code, rejected, _ = self.run_cli(request)
                self.assertEqual(code, 1)
                self.assertEqual(rejected["error"]["code"], "invalid_request")
                self.assertEqual(ledger.read_bytes(), before)
        frontmatter, records = PROTOCOL._load_records(ledger)
        records["Current Topics"][0]["record_revision"] = True
        ledger.write_bytes(PROTOCOL._render_records_ledger(frontmatter, records))
        corrupted = ledger.read_bytes()
        code, rejected, _ = self.run_cli(self.evolution_request(
            topic, operation="prepare-topic-update", expected_revision=1,
            expected_topic_revision=1, mutation={
                "type": "confirm-decision", "summary": "Never written.",
                "rationale": "Persisted boolean is invalid.",
            },
        ))
        self.assertEqual(code, 1)
        self.assertEqual(rejected["error"]["code"], "state_corrupt")
        self.assertEqual(ledger.read_bytes(), corrupted)

    def test_ticket07_reopen_retired_phase0_authority_allows_dependency_changes_via_cli(self) -> None:
        project = self.make_project("ticket07-reopen-retired-checkpoint", git=False)
        topic = self.bootstrap_topic(project)
        decision, revision, topic_revision = self.complete_update(
            project, topic, ledger_revision=1, topic_revision=1,
            mutation={
                "type": "confirm-decision", "summary": "Keep the API.",
                "rationale": "The Phase 0 authority is explicit.",
            },
        )
        self.publish_non_git_stage_entry_checkpoint(
            topic, ledger_revision=revision, topic_revision=topic_revision,
        )
        revision += 2
        _, revision, topic_revision = self.complete_current_topic_phase(
            topic, ledger_revision=revision, topic_revision=topic_revision,
            from_phase=0, to_phase=1,
        )
        code, reopened, stderr = self.run_cli(self.phase_request(
            topic, "reopen-phase", revision, topic_revision=topic_revision,
            affected_decision_ids=[decision["decision_id"]],
            review={decision["decision_id"]: "keep"},
            reason="Reopen Phase 0 with the old checkpoint retired.",
        ))
        self.assertEqual(code, 0, stderr)
        ledger = Path(str(topic["ledger_path"]))
        frontmatter, records = PROTOCOL._load_records(ledger)
        prerequisite_id = "topic-" + uuid.uuid4().hex
        add_topic(records, topic_id=prerequisite_id, root_slug="prerequisite",
            parent_topic_id=topic["topic_id"])
        ledger.write_bytes(PROTOCOL._render_records_ledger(frontmatter, records))
        code, created, stderr = self.run_cli(self.evolution_request(
            topic, operation="update-topic-dependency",
            expected_revision=reopened["ledger_revision"],
            expected_topic_revision=reopened["record_revision"], action="create",
            prerequisite_topic_id=prerequisite_id, requirement_kind="confirmed-decision",
            requirement_summary="A fresh Phase 0 gate.",
        ))
        self.assertEqual(code, 0, stderr)
        code, replaced, stderr = self.run_cli(self.evolution_request(
            topic, operation="update-topic-dependency",
            expected_revision=created["ledger_revision"],
            expected_topic_revision=reopened["record_revision"], action="replace",
            dependency_id=created["dependency_id"], expected_dependency_revision=1,
            prerequisite_topic_id=prerequisite_id, requirement_kind="phase-1-result",
            requirement_summary="A replacement gate after reopen.",
        ))
        self.assertEqual(code, 0, stderr)
        self.assertEqual(replaced["state"], "replace")

    def test_ticket07_malformed_dependency_discriminators_fail_stably_via_cli(self) -> None:
        project = self.make_project("ticket07-malformed-discriminators", git=False)
        topic = self.bootstrap_topic(project)
        ledger = Path(str(topic["ledger_path"]))
        before = ledger.read_bytes()
        for endpoint, prerequisite in (([], "target"), ({}, "target"), ("source", []), ("source", {})):
            request = self.handoff_request(
                topic, operation="prepare-handoff", ledger_revision=1,
                initial_dependencies=[{
                    "dependent_endpoint": endpoint,
                    "prerequisite_topic_ref": prerequisite,
                    "requirement_kind": "confirmed-decision",
                    "requirement_summary": "Bounded.",
                }],
            )
            code, rejected, _ = self.run_cli(request)
            self.assertEqual(code, 1)
            self.assertEqual(rejected["error"]["code"], "invalid_request")
            self.assertEqual(ledger.read_bytes(), before)
        prepared, child_ref = self.activate_child_handoff(topic)
        submit = self.handoff_request(
            topic, operation="submit-child-result", ledger_revision=5,
            owner_ref=child_ref, handoff_id=prepared["handoff_id"],
            attempt_id=prepared["attempt_id"], result_scope=["api"], summary="Typed.",
            authority_selection={"authority_kind": [], "authority_identity": None,
                                 "decision_ids": []},
        )
        submit["actor_topic_id"] = prepared["target_topic_id"]
        before = ledger.read_bytes()
        code, rejected, _ = self.run_cli(submit)
        self.assertEqual(code, 1)
        self.assertEqual(rejected["error"]["code"], "invalid_request")
        self.assertEqual(ledger.read_bytes(), before)

    def test_ticket07_published_authority_blocks_dependency_create_and_replace_via_cli(self) -> None:
        """A published Phase-0/1 authority must be explicitly reopened first."""
        for authority_phase in (0, 1):
            with self.subTest(authority_phase=authority_phase):
                project = self.make_project(
                    f"ticket07-published-authority-{authority_phase}", git=False,
                )
                topic = self.bootstrap_topic(project)
                if authority_phase == 0:
                    self.publish_non_git_stage_entry_checkpoint(
                        topic, ledger_revision=1,
                    )
                else:
                    _, revision, topic_revision = self.complete_update(
                        project, topic, ledger_revision=1, topic_revision=1,
                        mutation={"type": "confirm-decision", "summary": "Current.",
                                  "rationale": "Current."},
                    )
                    _, revision, topic_revision = self.complete_current_topic_phase(
                        topic, ledger_revision=revision, topic_revision=topic_revision,
                        from_phase=0, to_phase=1,
                    )
                    self.publish_non_git_stage_entry_checkpoint(
                        topic, ledger_revision=revision, topic_revision=topic_revision,
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
                create = self.evolution_request(
                    topic, operation="update-topic-dependency", expected_revision=revision,
                    expected_topic_revision=topic_record["record_revision"], action="create",
                    prerequisite_topic_id=prerequisite_id,
                    requirement_kind="confirmed-decision", requirement_summary="A new gate.",
                )
                code, rejected, _ = self.run_cli(create)
                self.assertEqual(code, 1)
                self.assertEqual(rejected["error"]["code"],
                                         "topic_dependency_published_authority_conflict")
                self.assertEqual(rejected["error"]["context"]["phase"], authority_phase)
                self.assertEqual(ledger.read_bytes(), before)
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
                replace = self.evolution_request(
                    topic, operation="update-topic-dependency", expected_revision=revision,
                    expected_topic_revision=topic_record["record_revision"], action="replace",
                    dependency_id=records["Topic Dependencies"][-1]["dependency_id"],
                    expected_dependency_revision=1, prerequisite_topic_id=prerequisite_id,
                    requirement_kind="phase-1-result", requirement_summary="Replacement.",
                )
                code, rejected, _ = self.run_cli(replace)
                self.assertEqual(code, 1)
                self.assertEqual(rejected["error"]["code"],
                                         "topic_dependency_published_authority_conflict")
                self.assertEqual(ledger.read_bytes(), before)

    def test_ticket07_nested_authority_selection_is_bounded_and_sorted_via_cli(self) -> None:
        project = self.make_project("ticket07-nested-authority-bounds", git=False)
        topic = self.bootstrap_topic(project)
        prepared, child_ref = self.activate_child_handoff(topic)
        request = self.handoff_request(
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
        code, rejected, _ = self.run_cli(request)
        self.assertEqual(code, 1)
        self.assertEqual(rejected["error"]["code"], "invalid_request")
        self.assertEqual(ledger.read_bytes(), before)

    def test_ticket07_gate_selection_requires_exact_closed_dependency_set_via_cli(self) -> None:
        project = self.make_project("ticket07-exact-gate-selection", git=False)
        topic = self.bootstrap_topic(project)
        prepared = self.prepare_child_handoff(topic, initial_dependencies=[{
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
            code, rejected, _ = self.run_cli(self.evolution_request(
                topic, operation="evaluate-topic-gate", basis_selection=selection
            ))
            self.assertEqual(code, 1)
            self.assertEqual(rejected["error"]["code"], "invalid_request")
            self.assertEqual(ledger.read_bytes(), before)

    def test_ticket07_active_closed_dependency_limit_is_atomic_via_cli(self) -> None:
        project = self.make_project("ticket07-active-closed-dependency-limit", git=False)
        topic = self.bootstrap_topic(project)
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
            descriptor = [{"decision_id": decision["decision_id"], "sha256": digest}]
            dependency_id = "DEP-" + uuid.uuid4().hex
            basis = {"basis_version": 1, "dependency_id": dependency_id, "prerequisite_topic_id": prerequisite_id, "requirement_kind": "confirmed-decision", "decision_authority": [{"decision_id": decision["decision_id"], "sha256": digest}], "authority": {"decision_set_digest": hashlib.sha256(PROTOCOL._canonical_json(descriptor).encode("utf-8")).hexdigest()}}
            records["Pending Items"].append({"item_id": decision["decision_id"], "item_kind": "decision", "topic_id": prerequisite_id, "data_json": PROTOCOL._canonical_json(decision)})
            records["Topic Dependencies"].append({"dependency_id": dependency_id, "record_revision": 2, "dependent_topic_id": str(topic["topic_id"]), "prerequisite_topic_id": prerequisite_id, "requirement_kind": "confirmed-decision", "requirement_summary": "A bounded prerequisite.", "relation_state": "active", "gate_state": "open", "accepted_basis_json": PROTOCOL._canonical_json(basis), "gate_reason_json": PROTOCOL._canonical_json({"kind": "atomic-release", "release_id": "00000000-0000-4000-8000-000000000000", "ledger_revision": 1})})
        ledger.write_bytes(PROTOCOL._render_records_ledger(frontmatter, records))
        before = ledger.read_bytes()
        request = self.evolution_request(topic, operation="update-topic-dependency", expected_revision=1, expected_topic_revision=1, action="create", prerequisite_topic_id=prerequisite_ids[64], requirement_kind="confirmed-decision", requirement_summary="The sixty-fifth prerequisite.")
        code, rejected, _ = self.run_cli(request)
        self.assertEqual(code, 1)
        self.assertEqual(rejected["error"]["code"], "invalid_request")
        self.assertEqual(ledger.read_bytes(), before)

    def test_ticket07_corrupt_persisted_authorities_fail_closed_via_cli(self) -> None:
        for authority_kind in ("checkpoint", "phase-result"):
            with self.subTest(authority_kind=authority_kind):
                project = self.make_project(f"ticket07-corrupt-{authority_kind}", git=False)
                topic = self.bootstrap_topic(project)
                if authority_kind == "checkpoint":
                    published = self.publish_non_git_stage_entry_checkpoint(topic, ledger_revision=1)
                    dependency_kind, authority_id, revision = "phase-0-checkpoint", published["checkpoint_id"], 3
                else:
                    decision, revision, topic_revision = self.complete_update(project, topic, ledger_revision=1, topic_revision=1, mutation={"type": "confirm-decision", "summary": "Current.", "rationale": "Current."})
                    completed, revision, _ = self.complete_current_topic_phase(topic, ledger_revision=revision, topic_revision=topic_revision, from_phase=0, to_phase=1)
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
                request = self.evolution_request(topic, operation="evaluate-topic-gate", basis_selection=[{"dependency_id": dependency_id, "authority_id": authority_id, "decision_ids": []}])
                request["actor_topic_id"] = dependent_id
                request["actor_conversation_ref"] = "codex-thread:dependent"
                code, rejected, _ = self.run_cli(request)
                self.assertEqual(code, 1)
                self.assertEqual(rejected["error"]["code"], "state_corrupt")
                self.assertEqual(ledger.read_bytes(), before)

    def test_open_gate_rejects_non_completed_retained_authority_states_via_cli(self) -> None:
        cases = (
            ("checkpoint-envelope", "phase-0-checkpoint", "broken"),
            ("checkpoint-data", "phase-0-checkpoint", "broken"),
            ("phase-result", "phase-1-result", "review-pending"),
        )
        for case, dependency_kind, invalid_state in cases:
            with self.subTest(case=case):
                project = self.make_project(f"open-gate-non-completed-{case}", git=False)
                topic = self.bootstrap_topic(project)
                if dependency_kind == "phase-0-checkpoint":
                    authority = self.publish_non_git_stage_entry_checkpoint(
                        topic, ledger_revision=1
                    )
                    authority_id = authority["checkpoint_id"]
                else:
                    decision, revision, topic_revision = self.complete_update(
                        project,
                        topic,
                        ledger_revision=1,
                        topic_revision=1,
                        mutation={
                            "type": "confirm-decision",
                            "summary": "Keep the completed phase authority.",
                            "rationale": "It is the release basis.",
                        },
                    )
                    authority, _, _ = self.complete_current_topic_phase(
                        topic,
                        ledger_revision=revision,
                        topic_revision=topic_revision,
                        from_phase=0,
                        to_phase=1,
                    )
                    authority_id = authority["phase_result_id"]
                ledger = Path(str(topic["ledger_path"]))
                ledger_revision = int(PROTOCOL._load_records(ledger)[0]["ledger_revision"])
                frontmatter, records = PROTOCOL._load_records(ledger)
                dependent_id = "topic-" + uuid.uuid4().hex
                dependency_id = "DEP-" + uuid.uuid4().hex
                records["Current Topics"].append({
                    "topic_id": dependent_id,
                    "record_revision": 1,
                    "root_slug": "dependent",
                    "parent_topic_id": str(topic["topic_id"]),
                    "current_phase": 0,
                    "phase_state": "active",
                    "review_state": "unreviewed",
                    "topic_state": "open",
                    "topic_document_path": None,
                })
                records["Conversation Bindings"].append({
                    "topic_id": dependent_id,
                    "conversation_ref": "codex-thread:dependent",
                    "binding_state": "active",
                    "record_revision": 1,
                    "handoff_id": None,
                    "attempt_id": None,
                })
                records["Topic Dependencies"].append({
                    "dependency_id": dependency_id,
                    "record_revision": 1,
                    "dependent_topic_id": dependent_id,
                    "prerequisite_topic_id": str(topic["topic_id"]),
                    "requirement_kind": dependency_kind,
                    "requirement_summary": "The completed authority remains required.",
                    "relation_state": "active",
                    "gate_state": "closed",
                    "accepted_basis_json": None,
                    "gate_reason_json": PROTOCOL._canonical_json({
                        "kind": "explicit-create",
                        "dependency_update_id": "00000000-0000-4000-8000-000000000000",
                        "ledger_revision": ledger_revision,
                    }),
                })
                ledger.write_bytes(PROTOCOL._render_records_ledger(frontmatter, records))
                selection = {
                    "dependency_id": dependency_id,
                    "authority_id": authority_id,
                    "decision_ids": [] if dependency_kind == "phase-0-checkpoint"
                    else [decision["decision_id"]],
                }
                evaluate = self.evolution_request(
                    topic, operation="evaluate-topic-gate", basis_selection=[selection]
                )
                evaluate["actor_topic_id"] = dependent_id
                evaluate["actor_conversation_ref"] = "codex-thread:dependent"
                code, evaluation, stderr = self.run_cli(evaluate)
                self.assertEqual(code, 0, stderr)
                release = self.evolution_request(
                    topic,
                    operation="release-topic-gate",
                    expected_revision=ledger_revision,
                    expected_topic_revision=1,
                    release_set=evaluation["release_set"],
                    release_set_sha256=evaluation["release_set_sha256"],
                )
                release["actor_topic_id"] = dependent_id
                release["actor_conversation_ref"] = "codex-thread:dependent"
                code, opened, stderr = self.run_cli(release)
                self.assertEqual(code, 0, stderr)
                self.assertEqual(opened["state"], "open")

                frontmatter, records = PROTOCOL._load_records(ledger)
                if case == "checkpoint-envelope":
                    target = records["Checkpoints"][0]
                    target["state"] = invalid_state
                elif case == "checkpoint-data":
                    target = records["Checkpoints"][0]
                    checkpoint = json.loads(str(target["data_json"]))
                    checkpoint["state"] = invalid_state
                    target["data_json"] = PROTOCOL._canonical_json(checkpoint)
                else:
                    target = next(
                        item for item in records["Phase Results"]
                        if item["result_id"] == authority_id
                    )
                    target["state"] = invalid_state
                ledger.write_bytes(PROTOCOL._render_records_ledger(frontmatter, records))
                before = ledger.read_bytes()
                read = self.evolution_request(topic, operation="read-topic")
                read["actor_topic_id"] = dependent_id
                read["actor_conversation_ref"] = "codex-thread:dependent"

                code, rejected, _ = self.run_cli(read)

                self.assertEqual(code, 1)
                self.assertEqual(rejected["error"]["code"], "state_corrupt")
                self.assertEqual(ledger.read_bytes(), before)

    def test_ticket07_forged_child_basis_authority_mismatch_fails_closed_via_cli(self) -> None:
        project = self.make_project("ticket07-forged-child-basis", git=False)
        topic = self.bootstrap_topic(project)
        prepared, child_ref = self.activate_child_handoff(topic, initial_dependencies=[{
            "dependent_endpoint": "source", "prerequisite_topic_ref": "target",
            "requirement_kind": "confirmed-decision", "requirement_summary": "The child selects the API.",
        }])
        ledger = Path(str(topic["ledger_path"]))
        frontmatter, records = PROTOCOL._load_records(ledger)
        decision = {"decision_id": "D-child", "summary": "Current.", "rationale": "Current.", "state": "confirmed", "evolution": "confirmed"}
        records["Pending Items"].append({"item_id": "D-child", "item_kind": "decision", "topic_id": prepared["target_topic_id"], "data_json": PROTOCOL._canonical_json(decision)})
        ledger.write_bytes(PROTOCOL._render_records_ledger(frontmatter, records))
        submit = self.handoff_request(topic, operation="submit-child-result", ledger_revision=5, owner_ref=child_ref, handoff_id=prepared["handoff_id"], attempt_id=prepared["attempt_id"], result_scope=["api"], summary="Current.", authority_selection={"authority_kind": "confirmed-decision", "authority_identity": None, "decision_ids": ["D-child"]})
        submit["actor_topic_id"] = prepared["target_topic_id"]
        code, claimed, stderr = self.run_cli(submit)
        self.assertEqual(code, 0, stderr)
        self.assertEqual(claimed["frozen_authority"]["decision_ids"], ["D-child"])
        release = {"dependency_id": prepared["initial_dependencies"][0]["dependency_id"], "authority_kind": "confirmed-decision", "authority_identity": None, "decision_ids": ["D-child"]}
        absorb = self.handoff_request(topic, operation="record-child-result", ledger_revision=6, handoff_id=prepared["handoff_id"], child_result_id=claimed["child_result_id"], effect="absorb", dependency_releases=[release])
        code, absorbed, stderr = self.run_cli(absorb)
        self.assertEqual(code, 0, stderr)
        self.assertEqual(absorbed["frozen_authority"], claimed["frozen_authority"])
        frontmatter, records = PROTOCOL._load_records(ledger)
        child = next(item for item in records["Phase Results"] if item["result_id"] == claimed["child_result_id"])
        frozen = json.loads(str(child["authority_json"]))
        frozen["decision_ids"] = ["D-forged"]
        child["authority_json"] = PROTOCOL._canonical_json(frozen)
        ledger.write_bytes(PROTOCOL._render_records_ledger(frontmatter, records))
        before = ledger.read_bytes()
        code, rejected, _ = self.run_cli(self.evolution_request(topic, operation="read-topic"))
        self.assertEqual(code, 1)
        self.assertEqual(rejected["error"]["code"], "state_corrupt")
        self.assertEqual(ledger.read_bytes(), before)

    def test_ticket07_concurrent_dependency_updates_commit_once_via_cli(self) -> None:
        project = self.make_project("ticket07-concurrent-dependency-updates", git=False)
        topic = self.bootstrap_topic(project)
        child = self.prepare_child_handoff(topic)
        code, created, stderr = self.run_cli(self.evolution_request(
            topic, operation="update-topic-dependency", expected_revision=2,
            expected_topic_revision=1, action="create",
            prerequisite_topic_id=child["target_topic_id"],
            requirement_kind="confirmed-decision", requirement_summary="The child selects the API.",
        ))
        self.assertEqual(code, 0, stderr)
        replace = self.evolution_request(
            topic, operation="update-topic-dependency", expected_revision=3,
            expected_topic_revision=1, action="replace",
            dependency_id=created["dependency_id"], expected_dependency_revision=1,
            prerequisite_topic_id=child["target_topic_id"],
            requirement_kind="phase-1-result", requirement_summary="The child completes Phase 1.",
        )
        cancel = self.evolution_request(
            topic, operation="update-topic-dependency", expected_revision=3,
            expected_topic_revision=1, action="cancel",
            dependency_id=created["dependency_id"], expected_dependency_revision=1,
        )

        def invoke(request: dict[str, object]) -> tuple[int, dict[str, object], str]:
            return self.run_cli(request)

        with ThreadPoolExecutor(max_workers=2) as executor:
            outcomes = list(executor.map(invoke, (replace, cancel)))
        successful = [response for code, response, _ in outcomes if code == 0]
        conflicted = [response for code, response, _ in outcomes if code == 1]
        self.assertEqual(len(successful), 1)
        self.assertEqual(len(conflicted), 1)
        self.assertEqual(conflicted[0]["error"]["code"], "ledger_revision_conflict")
        ledger = Path(str(topic["ledger_path"]))
        _, records = PROTOCOL._load_records(ledger)
        dependency = next(item for item in records["Topic Dependencies"] if item["dependency_id"] == created["dependency_id"])
        self.assertEqual(int(PROTOCOL._load_records(ledger)[0]["ledger_revision"]), 4)
        self.assertEqual(dependency["record_revision"], 2)
        self.assertIn(dependency["relation_state"], {"active", "cancelled"})
        self.assertEqual(len([event for event in records["Recent Events"] if event["event_type"].startswith("topic-dependency-")]), 2)

    def test_ticket07_injected_dependency_update_is_atomic_via_cli(self) -> None:
        for action in ("create", "replace", "cancel"):
            with self.subTest(action=action):
                project = self.make_project(f"ticket07-dependency-{action}-fault", git=False)
                topic = self.bootstrap_topic(project)
                child = self.prepare_child_handoff(topic)
                created = None
                if action != "create":
                    code, created, stderr = self.run_cli(self.evolution_request(
                        topic, operation="update-topic-dependency", expected_revision=2,
                        expected_topic_revision=1, action="create",
                        prerequisite_topic_id=child["target_topic_id"],
                        requirement_kind="confirmed-decision", requirement_summary="The child selects the API.",
                    ))
                    self.assertEqual(code, 0, stderr)
                request = self.evolution_request(
                    topic, operation="update-topic-dependency",
                    expected_revision=2 if action == "create" else 3,
                    expected_topic_revision=1, action=action,
                    **({"prerequisite_topic_id": child["target_topic_id"], "requirement_kind": "confirmed-decision", "requirement_summary": "The child selects the API."} if action == "create" else {"dependency_id": created["dependency_id"], "expected_dependency_revision": 1, **({"prerequisite_topic_id": child["target_topic_id"], "requirement_kind": "phase-1-result", "requirement_summary": "The child completes Phase 1."} if action == "replace" else {})}),
                )
                ledger = Path(str(topic["ledger_path"]))
                before = ledger.read_bytes()
                code, failed, _ = self.run_cli(request, failpoint="topic-dependency-before-ledger-write")
                self.assertEqual(code, 1)
                self.assertEqual(failed["error"]["code"], "injected_failure")
                self.assertEqual(ledger.read_bytes(), before)
                code, completed, stderr = self.run_cli(request)
                self.assertEqual(code, 0, stderr)
                code, replay, stderr = self.run_cli(request)
                self.assertEqual(code, 0, stderr)
                self.assertTrue(replay["idempotent_replay"])
                self.assertEqual(replay["dependency_id"], completed["dependency_id"])

    def test_ticket07_dependency_update_reasons_keep_exact_operation_ids_via_cli(self) -> None:
        project = self.make_project("ticket07-dependency-operation-identities", git=False)
        topic = self.bootstrap_topic(project)
        child = self.prepare_child_handoff(topic)
        ledger = Path(str(topic["ledger_path"]))
        create = self.evolution_request(topic, operation="update-topic-dependency", expected_revision=2, expected_topic_revision=1, action="create", prerequisite_topic_id=child["target_topic_id"], requirement_kind="confirmed-decision", requirement_summary="The child selects the API.")
        code, created, stderr = self.run_cli(create)
        self.assertEqual(code, 0, stderr)
        replace = self.evolution_request(topic, operation="update-topic-dependency", expected_revision=3, expected_topic_revision=1, action="replace", dependency_id=created["dependency_id"], expected_dependency_revision=1, prerequisite_topic_id=child["target_topic_id"], requirement_kind="phase-1-result", requirement_summary="The child completes Phase 1.")
        code, _, stderr = self.run_cli(replace)
        self.assertEqual(code, 0, stderr)
        cancel = self.evolution_request(topic, operation="update-topic-dependency", expected_revision=4, expected_topic_revision=1, action="cancel", dependency_id=created["dependency_id"], expected_dependency_revision=2)
        code, _, stderr = self.run_cli(cancel)
        self.assertEqual(code, 0, stderr)
        _, records = PROTOCOL._load_records(ledger)
        dependency = next(item for item in records["Topic Dependencies"] if item["dependency_id"] == created["dependency_id"])
        reason = json.loads(str(dependency["gate_reason_json"]))
        self.assertEqual(reason["kind"], "explicit-cancel")
        self.assertEqual(reason["dependency_update_id"], cancel["idempotency_key"])
        self.assertNotEqual(reason["dependency_update_id"], str(dependency["record_revision"]))
        before = ledger.read_bytes()
        code, replay, stderr = self.run_cli(cancel)
        self.assertEqual(code, 0, stderr)
        self.assertTrue(replay["idempotent_replay"])
        self.assertEqual(ledger.read_bytes(), before)

    def test_dependency_replace_event_retains_canonical_before_and_after_definitions_via_cli(self) -> None:
        project = self.make_project("dependency-replace-event-history", git=False)
        topic = self.bootstrap_topic(project)
        first = self.prepare_child_handoff(topic)
        second = self.prepare_child_handoff(topic, ledger_revision=2)
        ledger = Path(str(topic["ledger_path"]))
        code, created, stderr = self.run_cli(self.evolution_request(
            topic,
            operation="update-topic-dependency",
            expected_revision=3,
            expected_topic_revision=1,
            action="create",
            prerequisite_topic_id=first["target_topic_id"],
            requirement_kind="confirmed-decision",
            requirement_summary="The first child decision is required.",
        ))
        self.assertEqual(code, 0, stderr)
        _, before_records = PROTOCOL._load_records(ledger)
        before_definition = dict(next(
            item for item in before_records["Topic Dependencies"]
            if item["dependency_id"] == created["dependency_id"]
        ))
        code, replaced, stderr = self.run_cli(self.evolution_request(
            topic,
            operation="update-topic-dependency",
            expected_revision=4,
            expected_topic_revision=1,
            action="replace",
            dependency_id=created["dependency_id"],
            expected_dependency_revision=1,
            prerequisite_topic_id=second["target_topic_id"],
            requirement_kind="phase-1-result",
            requirement_summary="The second child Phase 1 result is required.",
        ))
        self.assertEqual(code, 0, stderr)
        _, records = PROTOCOL._load_records(ledger)
        after_definition = dict(next(
            item for item in records["Topic Dependencies"]
            if item["dependency_id"] == created["dependency_id"]
        ))
        replace_event = next(
            event for event in records["Recent Events"]
            if event["event_type"] == "topic-dependency-replace"
        )
        event_result = json.loads(str(replace_event["result_json"]))

        self.assertEqual(replaced["dependency_before"], before_definition)
        self.assertEqual(replaced["dependency_after"], after_definition)
        self.assertEqual(event_result["dependency_before"], before_definition)
        self.assertEqual(event_result["dependency_after"], after_definition)
        self.assertEqual(event_result["dependency_before"]["record_revision"], 1)
        self.assertEqual(event_result["dependency_after"]["record_revision"], 2)

    def test_ticket07_direct_release_reason_keeps_exact_operation_id_via_cli(self) -> None:
        project = self.make_project("ticket07-direct-release-identity", git=False)
        topic = self.bootstrap_topic(project)
        child = self.prepare_child_handoff(topic, initial_dependencies=[{"dependent_endpoint": "source", "prerequisite_topic_ref": "target", "requirement_kind": "confirmed-decision", "requirement_summary": "The child selects the API."}])
        ledger = Path(str(topic["ledger_path"]))
        frontmatter, records = PROTOCOL._load_records(ledger)
        decision = {"decision_id": "D-child", "summary": "Use typed requests.", "rationale": "Current.", "state": "confirmed", "evolution": "confirmed"}
        records["Pending Items"].append({"item_id": "D-child", "item_kind": "decision", "topic_id": child["target_topic_id"], "data_json": PROTOCOL._canonical_json(decision)})
        ledger.write_bytes(PROTOCOL._render_records_ledger(frontmatter, records))
        dependency_id = child["initial_dependencies"][0]["dependency_id"]
        code, evaluation, stderr = self.run_cli(self.evolution_request(topic, operation="evaluate-topic-gate", basis_selection=[{"dependency_id": dependency_id, "decision_ids": ["D-child"]}]))
        self.assertEqual(code, 0, stderr)
        release = self.evolution_request(topic, operation="release-topic-gate", expected_revision=2, expected_topic_revision=1, release_set=evaluation["release_set"], release_set_sha256=evaluation["release_set_sha256"])
        code, _, stderr = self.run_cli(release)
        self.assertEqual(code, 0, stderr)
        _, records = PROTOCOL._load_records(ledger)
        dependency = next(item for item in records["Topic Dependencies"] if item["dependency_id"] == dependency_id)
        self.assertEqual(json.loads(str(dependency["gate_reason_json"]))["release_id"], release["idempotency_key"])
        before = ledger.read_bytes()
        code, replay, stderr = self.run_cli(release)
        self.assertEqual(code, 0, stderr)
        self.assertTrue(replay["idempotent_replay"])
        self.assertEqual(ledger.read_bytes(), before)

    def test_ticket07_reopen_reason_keeps_exact_result_and_operation_ids_via_cli(self) -> None:
        project = self.make_project("ticket07-reopen-operation-identity", git=False)
        topic = self.bootstrap_topic(project)
        decision, ledger_revision, topic_revision = self.complete_update(project, topic, ledger_revision=1, topic_revision=1, mutation={"type": "confirm-decision", "summary": "Keep the API.", "rationale": "Phase 1 depends on it."})
        completed, ledger_revision, topic_revision = self.complete_current_topic_phase(topic, ledger_revision=ledger_revision, topic_revision=topic_revision, from_phase=0, to_phase=1)
        ledger = Path(str(topic["ledger_path"]))
        b_id = "topic-" + "e" * 32
        frontmatter, records = PROTOCOL._load_records(ledger)
        records["Current Topics"].append({"topic_id": b_id, "record_revision": 1, "root_slug": "dependent", "parent_topic_id": str(topic["topic_id"]), "current_phase": 1, "phase_state": "active", "review_state": "unreviewed", "topic_state": "open", "topic_document_path": None})
        records["Conversation Bindings"].append({"topic_id": b_id, "conversation_ref": "codex-thread:dependent", "binding_state": "active", "record_revision": 1, "handoff_id": None, "attempt_id": None})
        dependency_id = "DEP-" + "d" * 32
        records["Topic Dependencies"].append({"dependency_id": dependency_id, "record_revision": 1, "dependent_topic_id": b_id, "prerequisite_topic_id": str(topic["topic_id"]), "requirement_kind": "phase-1-result", "requirement_summary": "The Phase 1 result remains current.", "relation_state": "active", "gate_state": "closed", "accepted_basis_json": None, "gate_reason_json": PROTOCOL._canonical_json({"kind": "explicit-create", "dependency_update_id": "00000000-0000-4000-8000-000000000000", "ledger_revision": 1})})
        ledger.write_bytes(PROTOCOL._render_records_ledger(frontmatter, records))
        code, evaluation, stderr = self.run_cli({**self.evolution_request(topic, operation="evaluate-topic-gate", basis_selection=[{"dependency_id": dependency_id, "authority_id": completed["phase_result_id"], "decision_ids": [decision["decision_id"]]}]), "actor_topic_id": b_id, "actor_conversation_ref": "codex-thread:dependent"})
        self.assertEqual(code, 0, stderr)
        code, _, stderr = self.run_cli({**self.evolution_request(topic, operation="release-topic-gate", expected_revision=ledger_revision, expected_topic_revision=1, release_set=evaluation["release_set"], release_set_sha256=evaluation["release_set_sha256"]), "actor_topic_id": b_id, "actor_conversation_ref": "codex-thread:dependent"})
        self.assertEqual(code, 0, stderr)
        reopen = self.phase_request(topic, "reopen-phase", ledger_revision + 1, topic_revision=topic_revision, affected_decision_ids=[decision["decision_id"]], review={decision["decision_id"]: "adjust"}, reason="The API changed.")
        code, reopened, stderr = self.run_cli(reopen)
        self.assertEqual(code, 0, stderr)
        _, records = PROTOCOL._load_records(ledger)
        dependency = next(item for item in records["Topic Dependencies"] if item["dependency_id"] == dependency_id)
        reason = json.loads(str(dependency["gate_reason_json"]))
        self.assertEqual(reason["reopen_id"], reopen["idempotency_key"])
        self.assertEqual(reason["invalidated_result_ids"], [completed["phase_result_id"]])
        self.assertEqual(reason["affected_decision_ids"], [decision["decision_id"]])
        before = ledger.read_bytes()
        code, replay, stderr = self.run_cli(reopen)
        self.assertEqual(code, 0, stderr)
        self.assertTrue(replay["idempotent_replay"])
        self.assertEqual(ledger.read_bytes(), before)

    def test_ticket07_reopen_all_keep_recloses_result_gate_via_cli(self) -> None:
        project = self.make_project("ticket07-reopen-all-keep", git=False)
        topic = self.bootstrap_topic(project)
        decision, revision, topic_revision = self.complete_update(project, topic, ledger_revision=1, topic_revision=1, mutation={"type": "confirm-decision", "summary": "Keep the API.", "rationale": "Phase 1 depends on it."})
        completed, revision, topic_revision = self.complete_current_topic_phase(topic, ledger_revision=revision, topic_revision=topic_revision, from_phase=0, to_phase=1)
        ledger = Path(str(topic["ledger_path"]))
        b_id = "topic-" + "f" * 32
        frontmatter, records = PROTOCOL._load_records(ledger)
        records["Current Topics"].append({"topic_id": b_id, "record_revision": 1, "root_slug": "dependent", "parent_topic_id": str(topic["topic_id"]), "current_phase": 1, "phase_state": "active", "review_state": "unreviewed", "topic_state": "open", "topic_document_path": None})
        records["Conversation Bindings"].append({"topic_id": b_id, "conversation_ref": "codex-thread:dependent", "binding_state": "active", "record_revision": 1, "handoff_id": None, "attempt_id": None})
        dependency_id = "DEP-" + "e" * 32
        records["Topic Dependencies"].append({"dependency_id": dependency_id, "record_revision": 1, "dependent_topic_id": b_id, "prerequisite_topic_id": str(topic["topic_id"]), "requirement_kind": "phase-1-result", "requirement_summary": "The Phase 1 result remains current.", "relation_state": "active", "gate_state": "closed", "accepted_basis_json": None, "gate_reason_json": PROTOCOL._canonical_json({"kind": "explicit-create", "dependency_update_id": "00000000-0000-4000-8000-000000000000", "ledger_revision": 1})})
        ledger.write_bytes(PROTOCOL._render_records_ledger(frontmatter, records))
        code, evaluation, stderr = self.run_cli({**self.evolution_request(topic, operation="evaluate-topic-gate", basis_selection=[{"dependency_id": dependency_id, "authority_id": completed["phase_result_id"], "decision_ids": [decision["decision_id"]]}]), "actor_topic_id": b_id, "actor_conversation_ref": "codex-thread:dependent"})
        self.assertEqual(code, 0, stderr)
        code, _, stderr = self.run_cli({**self.evolution_request(topic, operation="release-topic-gate", expected_revision=revision, expected_topic_revision=1, release_set=evaluation["release_set"], release_set_sha256=evaluation["release_set_sha256"]), "actor_topic_id": b_id, "actor_conversation_ref": "codex-thread:dependent"})
        self.assertEqual(code, 0, stderr)
        decision_before = next(
            item["data_json"] for item in PROTOCOL._load_records(ledger)[1]["Pending Items"]
            if item.get("item_id") == decision["decision_id"]
        )
        reopen = self.phase_request(topic, "reopen-phase", revision + 1, topic_revision=topic_revision, affected_decision_ids=[decision["decision_id"]], review={decision["decision_id"]: "keep"}, reason="Re-evaluate the completed result.")
        code, _, stderr = self.run_cli(reopen)
        self.assertEqual(code, 0, stderr)
        _, records = PROTOCOL._load_records(ledger)
        dependency = next(item for item in records["Topic Dependencies"] if item["dependency_id"] == dependency_id)
        reason = json.loads(str(dependency["gate_reason_json"]))
        self.assertEqual(dependency["gate_state"], "closed")
        self.assertEqual(reason["reopen_id"], reopen["idempotency_key"])
        self.assertEqual(reason["invalidated_result_ids"], [completed["phase_result_id"]])
        self.assertEqual(reason["affected_decision_ids"], [])
        self.assertEqual(next(item for item in records["Current Topics"] if item["topic_id"] == b_id)["phase_state"], "active")
        self.assertEqual(
            next(item["data_json"] for item in records["Pending Items"]
                 if item.get("item_id") == decision["decision_id"]),
            decision_before,
        )
        code, current, stderr = self.run_cli(self.evolution_request(topic, operation="read-topic"))
        self.assertEqual(code, 0, stderr)
        self.assertEqual(current["current_phase"], 0)
        before = ledger.read_bytes()
        code, replay, stderr = self.run_cli(reopen)
        self.assertEqual(code, 0, stderr)
        self.assertTrue(replay["idempotent_replay"])
        self.assertEqual(ledger.read_bytes(), before)
