"""Public CLI Ticket-07 checkpoint, authority, GC, and migration scenarios."""

from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).parent))

import discussion_protocol as PROTOCOL
from test_discussion_protocol import hashlib, json, os, subprocess, uuid, ThreadPoolExecutor
from test_topic_dependency_support import (
    TopicDependencyScenarioMixin, add_binding, add_closed_dependency, add_topic,
)


class TopicDependencyCheckpointCliTests(TopicDependencyScenarioMixin, unittest.TestCase):
    def test_ticket07_cross_topic_checkpoint_mutation_is_rejected_atomically_via_cli(self) -> None:
        project = self.make_project("ticket07-cross-topic-checkpoint", git=False)
        topic = self.bootstrap_topic(project)
        checkpoint = self.publish_non_git_stage_entry_checkpoint(topic, ledger_revision=1)
        ledger = Path(str(topic["ledger_path"]))
        frontmatter, records = PROTOCOL._load_records(ledger)
        dependent_id = "topic-" + uuid.uuid4().hex
        add_topic(records, topic_id=dependent_id, root_slug="checkpoint-observer",
            parent_topic_id=str(topic["topic_id"]))
        add_binding(records, topic_id=dependent_id,
            conversation_ref="codex-thread:checkpoint-observer")
        ledger.write_bytes(PROTOCOL._render_records_ledger(frontmatter, records))
        before = ledger.read_bytes()
        request = self.checkpoint_request(
            topic, operation="mark-checkpoint-broken", ledger_revision=3,
            checkpoint_id=checkpoint["checkpoint_id"], expected_checkpoint_revision=2,
            broken_identity=checkpoint["snapshot_digest"],
            reason="A sibling must not mutate this checkpoint.",
        )
        request["actor_topic_id"] = dependent_id
        request["actor_conversation_ref"] = "codex-thread:checkpoint-observer"
        code, rejected, _ = self.run_cli(request)
        self.assertEqual(code, 1)
        self.assertEqual(rejected["error"]["code"], "checkpoint_identity_conflict")
        self.assertEqual(ledger.read_bytes(), before)
        read = self.evolution_request(topic, operation="read-topic")
        read["actor_topic_id"] = dependent_id
        read["actor_conversation_ref"] = "codex-thread:checkpoint-observer"
        code, current, stderr = self.run_cli(read)
        self.assertEqual(code, 0, stderr)
        self.assertEqual(current["derived_gate_state"], "open")

    def test_ticket07_large_checkpoint_candidate_releases_only_bounded_basis_via_cli(self) -> None:
        """A legal 65-decision authority remains readable; only its basis is bounded."""
        for git in (False, True):
            with self.subTest(storage_kind="git" if git else "non-git"):
                project = self.make_project(
                    f"ticket07-large-checkpoint-candidate-{git}", git=git,
                )
                if git:
                    (project / "base.txt").write_text("base\n", encoding="utf-8")
                    subprocess.run(["git", "-C", str(project), "add", "base.txt"], check=True)
                    subprocess.run(
                        ["git", "-C", str(project), "-c", "user.name=Test",
                         "-c", "user.email=test@example.com", "commit", "-qm", "base"],
                        check=True,
                    )
                topic = self.bootstrap_topic(project)
                ledger = Path(str(topic["ledger_path"]))
                frontmatter, records = PROTOCOL._load_records(ledger)
                decision_ids = [f"D-{index:03d}" for index in range(65)]
                for decision_id in decision_ids:
                    decision = {
                        "decision_id": decision_id, "summary": f"Keep {decision_id}.",
                        "rationale": "The candidate remains authoritative.",
                        "state": "confirmed", "evolution": "confirmed",
                    }
                    records["Pending Items"].append({
                        "item_id": decision_id, "item_kind": "decision",
                        "topic_id": topic["topic_id"],
                        "data_json": PROTOCOL._canonical_json(decision),
                    })
                ledger.write_bytes(PROTOCOL._render_records_ledger(frontmatter, records))
                prepared = self.prepare_checkpoint(
                    topic, ledger_revision=1, purpose="stage-entry",
                    base_ref="HEAD" if git else "project-root",
                )
                if git:
                    checkpoint = self.publish_git_checkpoint(
                        project, topic, prepared, ledger_revision=2,
                    )
                else:
                    code, checkpoint, stderr = self.run_cli(
                        self.checkpoint_request(
                            topic, operation="publish-non-git-checkpoint",
                            ledger_revision=2, checkpoint_id=prepared["checkpoint_id"],
                            expected_checkpoint_revision=1,
                        )
                    )
                    self.assertEqual(code, 0, stderr)
                frontmatter, records = PROTOCOL._load_records(ledger)
                dependent_id = "topic-" + uuid.uuid4().hex
                dependency_id = "DEP-" + uuid.uuid4().hex
                add_topic(records, topic_id=dependent_id, root_slug="bounded-dependent",
                    parent_topic_id=str(topic["topic_id"]))
                add_binding(records, topic_id=dependent_id,
                    conversation_ref="codex-thread:bounded-dependent")
                add_closed_dependency(
                    records, dependency_id=dependency_id, dependent_topic_id=dependent_id,
                    prerequisite_topic_id=str(topic["topic_id"]),
                    requirement_kind="phase-0-checkpoint",
                    requirement_summary="Choose a bounded subset from the authority.",
                    gate_reason_json=PROTOCOL._canonical_json({
                        "kind": "explicit-create",
                        "dependency_update_id": "00000000-0000-4000-8000-000000000000",
                        "ledger_revision": 3,
                    }),
                )
                ledger.write_bytes(PROTOCOL._render_records_ledger(frontmatter, records))
                bad = self.evolution_request(
                    topic, operation="evaluate-topic-gate", basis_selection=[{
                        "dependency_id": dependency_id,
                        "authority_id": checkpoint["checkpoint_id"],
                        "decision_ids": decision_ids,
                    }],
                )
                bad["actor_topic_id"] = dependent_id
                bad["actor_conversation_ref"] = "codex-thread:bounded-dependent"
                before = ledger.read_bytes()
                code, rejected, _ = self.run_cli(bad)
                self.assertEqual(code, 1)
                self.assertEqual(rejected["error"]["code"], "invalid_request")
                self.assertEqual(ledger.read_bytes(), before)
                good = {**bad, "basis_selection": [{
                    "dependency_id": dependency_id,
                    "authority_id": checkpoint["checkpoint_id"],
                    "decision_ids": decision_ids[:64],
                }]}
                code, evaluation, stderr = self.run_cli(good)
                self.assertEqual(code, 0, stderr)
                release = self.evolution_request(
                    topic, operation="release-topic-gate", expected_revision=3,
                    expected_topic_revision=1, release_set=evaluation["release_set"],
                    release_set_sha256=evaluation["release_set_sha256"],
                )
                release["actor_topic_id"] = dependent_id
                release["actor_conversation_ref"] = "codex-thread:bounded-dependent"
                code, opened, stderr = self.run_cli(release)
                self.assertEqual(code, 0, stderr)
                self.assertEqual(opened["state"], "open")
                code, reread, stderr = self.run_cli(
                    self.evolution_request(
                        {**topic, "topic_id": dependent_id}, operation="read-topic",
                        owner_ref="codex-thread:bounded-dependent",
                    )
                )
                self.assertEqual(code, 0, stderr)
                self.assertEqual(reread["derived_gate_state"], "open")

    def test_ticket07_checkpoint_supersession_recloses_direct_open_gates_via_cli(self) -> None:
        """A newer Phase-0 checkpoint retires only the released CP1 basis."""
        for git, reconcile in ((False, False), (True, False), (False, True)):
            with self.subTest(
                storage_kind="git" if git else "non-git", reconcile=reconcile,
            ):
                project = self.make_project(
                    f"ticket07-checkpoint-supersession-{git}-{reconcile}", git=git,
                )
                if git:
                    (project / "base.txt").write_text("base\n", encoding="utf-8")
                    subprocess.run(["git", "-C", str(project), "add", "base.txt"], check=True)
                    subprocess.run(
                        ["git", "-C", str(project), "-c", "user.name=Test",
                         "-c", "user.email=test@example.com", "commit", "-qm", "base"],
                        check=True,
                    )
                topic = self.bootstrap_topic(project)

                def publish(revision: int, topic_revision: int) -> dict[str, object]:
                    prepared = self.prepare_checkpoint(
                        topic, ledger_revision=revision, topic_revision=topic_revision,
                        purpose="stage-entry", base_ref="HEAD" if git else "project-root",
                    )
                    if git:
                        return self.publish_git_checkpoint(
                            project, topic, prepared, ledger_revision=revision + 1,
                            topic_revision=topic_revision,
                        )
                    code, result, stderr = self.run_cli(
                        self.checkpoint_request(
                            topic, operation="publish-non-git-checkpoint",
                            ledger_revision=revision + 1, topic_revision=topic_revision,
                            checkpoint_id=prepared["checkpoint_id"],
                            expected_checkpoint_revision=prepared["checkpoint_record_revision"],
                        )
                    )
                    self.assertEqual(code, 0, stderr)
                    return result

                checkpoint_one = publish(1, 1)
                ledger = Path(str(topic["ledger_path"]))
                frontmatter, records = PROTOCOL._load_records(ledger)
                dependent_id = "topic-" + uuid.uuid4().hex
                add_topic(records, topic_id=dependent_id, root_slug="dependent",
                    parent_topic_id=str(topic["topic_id"]))
                add_binding(records, topic_id=dependent_id,
                    conversation_ref="codex-thread:dependent")
                ledger.write_bytes(PROTOCOL._render_records_ledger(frontmatter, records))
                create = self.evolution_request(
                    topic, operation="update-topic-dependency",
                    expected_revision=3, expected_topic_revision=1, action="create",
                    prerequisite_topic_id=str(topic["topic_id"]),
                    requirement_kind="phase-0-checkpoint",
                    requirement_summary="The first checkpoint is frozen authority.",
                )
                create["actor_topic_id"] = dependent_id
                create["actor_conversation_ref"] = "codex-thread:dependent"
                code, created, stderr = self.run_cli(create)
                self.assertEqual(code, 0, stderr)
                dependency_id = created["dependency_id"]
                evaluate = self.evolution_request(
                    topic, operation="evaluate-topic-gate", basis_selection=[{
                        "dependency_id": dependency_id,
                        "authority_id": checkpoint_one["checkpoint_id"],
                        "decision_ids": [],
                    }],
                )
                evaluate["actor_topic_id"] = dependent_id
                evaluate["actor_conversation_ref"] = "codex-thread:dependent"
                code, proposal, stderr = self.run_cli(evaluate)
                self.assertEqual(code, 0, stderr)
                release = self.evolution_request(
                    topic, operation="release-topic-gate", expected_revision=4,
                    expected_topic_revision=1, release_set=proposal["release_set"],
                    release_set_sha256=proposal["release_set_sha256"],
                )
                release["actor_topic_id"] = dependent_id
                release["actor_conversation_ref"] = "codex-thread:dependent"
                code, opened, stderr = self.run_cli(release)
                self.assertEqual(code, 0, stderr)
                self.assertEqual(opened["state"], "open")
                _, publication_revision, publication_topic_revision = self.complete_update(
                    project, topic, ledger_revision=5, topic_revision=1, mutation={
                        "type": "confirm-decision", "summary": "A later conclusion.",
                        "rationale": "CP2 must freeze the updated authority.",
                    },
                )
                if not reconcile:
                    checkpoint_two = publish(publication_revision, publication_topic_revision)
                else:
                    prepared_two = self.prepare_checkpoint(
                        topic, ledger_revision=publication_revision,
                        topic_revision=publication_topic_revision,
                        purpose="stage-entry", base_ref="project-root",
                    )
                    publish_two = self.checkpoint_request(
                        topic, operation="publish-non-git-checkpoint",
                        ledger_revision=publication_revision + 1,
                        topic_revision=publication_topic_revision,
                        checkpoint_id=prepared_two["checkpoint_id"],
                        expected_checkpoint_revision=prepared_two["checkpoint_record_revision"],
                    )
                    before_publish = ledger.read_bytes()
                    code, rejected, _ = self.run_cli(
                        publish_two,
                        failpoint="checkpoint-before-completed-ledger-write",
                    )
                    self.assertEqual(code, 1)
                    self.assertEqual(rejected["error"]["code"], "injected_failure")
                    self.assertEqual(ledger.read_bytes(), before_publish)
                    outcome = self.checkpoint_request(
                        topic, operation="record-checkpoint-outcome-unknown",
                        ledger_revision=publication_revision + 1,
                        topic_revision=publication_topic_revision,
                        checkpoint_id=prepared_two["checkpoint_id"],
                        expected_checkpoint_revision=1,
                    )
                    code, unknown, stderr = self.run_cli(outcome)
                    self.assertEqual(code, 0, stderr)
                    reconcile_request = self.checkpoint_request(
                        topic, operation="reconcile-non-git-checkpoint",
                        ledger_revision=unknown["ledger_revision"],
                        topic_revision=publication_topic_revision,
                        checkpoint_id=prepared_two["checkpoint_id"],
                        expected_checkpoint_revision=unknown["checkpoint_record_revision"],
                    )
                    code, checkpoint_two, stderr = self.run_cli(reconcile_request)
                    self.assertEqual(code, 0, stderr)
                self.assertEqual(checkpoint_two["reclosed_dependency_ids"], [dependency_id])
                read = self.evolution_request(topic, operation="read-topic")
                read["actor_topic_id"] = dependent_id
                read["actor_conversation_ref"] = "codex-thread:dependent"
                code, current, stderr = self.run_cli(read)
                self.assertEqual(code, 0, stderr)
                dependency = current["topic_dependencies"][0]
                self.assertEqual(dependency["gate_state"], "closed")
                basis = json.loads(dependency["accepted_basis_json"])
                self.assertEqual(
                    basis["authority"]["checkpoint_id"], checkpoint_one["checkpoint_id"],
                )

    def test_ticket07_enforcing_open_basis_cannot_keep_historical_edge_via_cli(self) -> None:
        project = self.make_project("ticket07-enforcing-current-basis", git=False)
        topic = self.bootstrap_topic(project)
        self.publish_non_git_stage_entry_checkpoint(topic, ledger_revision=1)
        ledger = Path(str(topic["ledger_path"]))
        frontmatter, records = PROTOCOL._load_records(ledger)
        prerequisite_id = "topic-" + "a" * 32
        dependent_id = "topic-" + "c" * 32
        add_topic(records, topic_id=prerequisite_id, root_slug="decision-source",
            parent_topic_id=str(topic["topic_id"]))
        add_topic(records, topic_id=dependent_id, root_slug="dependent",
            parent_topic_id=str(topic["topic_id"]))
        add_binding(records, topic_id=dependent_id, conversation_ref="codex-thread:dependent")
        decision = {
            "decision_id": "D-a", "summary": "Keep A.", "rationale": "Current.",
            "state": "confirmed", "evolution": "confirmed",
        }
        digest = hashlib.sha256(PROTOCOL._canonical_json(decision).encode("utf-8")).hexdigest()
        pairs = [{"decision_id": "D-a", "sha256": digest}]
        records["Pending Items"].append({
            "item_id": "D-a", "item_kind": "decision", "topic_id": prerequisite_id,
            "data_json": PROTOCOL._canonical_json(decision),
        })
        dependency_id = "DEP-" + "d" * 32
        add_closed_dependency(
            records, dependency_id=dependency_id, dependent_topic_id=dependent_id,
            prerequisite_topic_id=str(topic["topic_id"]),
            requirement_kind="phase-0-checkpoint", requirement_summary="Checkpoint C.",
            gate_reason_json=PROTOCOL._canonical_json({
                "kind": "atomic-release", "release_id": "00000000-0000-4000-8000-000000000000",
                "ledger_revision": 1,
            }),
        )
        dependency = records["Topic Dependencies"][-1]
        dependency["record_revision"] = 2
        dependency["gate_state"] = "open"
        dependency["accepted_basis_json"] = PROTOCOL._canonical_json({
            "basis_version": 1, "dependency_id": dependency_id,
            "prerequisite_topic_id": prerequisite_id,
            "requirement_kind": "confirmed-decision", "decision_authority": pairs,
            "authority": {
                "decision_set_digest": hashlib.sha256(
                    PROTOCOL._canonical_json(pairs).encode("utf-8")
                ).hexdigest(),
            },
        })
        ledger.write_bytes(PROTOCOL._render_records_ledger(frontmatter, records))
        before = ledger.read_bytes()
        request = self.evolution_request(topic, operation="read-topic")
        request["actor_topic_id"] = dependent_id
        request["actor_conversation_ref"] = "codex-thread:dependent"
        code, rejected, _ = self.run_cli(request)
        self.assertEqual(code, 1)
        self.assertEqual(rejected["error"]["code"], "state_corrupt")
        self.assertEqual(ledger.read_bytes(), before)

    def test_ticket07_stale_checkpoint_release_is_rejected_via_cli(self) -> None:
        project = self.make_project("ticket07-stale-checkpoint-release", git=False)
        topic = self.bootstrap_topic(project)
        checkpoint = self.publish_non_git_stage_entry_checkpoint(topic, ledger_revision=1)
        ledger = Path(str(topic["ledger_path"]))
        frontmatter, records = PROTOCOL._load_records(ledger)
        dependent_id = "topic-" + "1" * 32
        add_topic(records, topic_id=dependent_id, root_slug="dependent", parent_topic_id=str(topic["topic_id"]))
        add_binding(records, topic_id=dependent_id, conversation_ref="codex-thread:dependent")
        dependency_id = "DEP-" + "a" * 32
        add_closed_dependency(records, dependency_id=dependency_id, dependent_topic_id=dependent_id,
            prerequisite_topic_id=str(topic["topic_id"]), requirement_kind="phase-0-checkpoint",
            requirement_summary="The root checkpoint is current.", gate_reason_json=PROTOCOL._canonical_json({"kind": "explicit-create", "dependency_update_id": "00000000-0000-4000-8000-000000000000", "ledger_revision": 1}))
        ledger.write_bytes(PROTOCOL._render_records_ledger(frontmatter, records))
        gate = self.evolution_request(topic, operation="evaluate-topic-gate", basis_selection=[{"dependency_id": dependency_id, "authority_id": checkpoint["checkpoint_id"], "decision_ids": []}])
        gate["actor_topic_id"] = dependent_id
        gate["actor_conversation_ref"] = "codex-thread:dependent"
        code, evaluation, stderr = self.run_cli(gate)
        self.assertEqual(code, 0, stderr)
        _, records = PROTOCOL._load_records(ledger)
        records["Checkpoints"][0]["state"] = "superseded"
        ledger.write_bytes(PROTOCOL._render_records_ledger(frontmatter, records))
        before = ledger.read_bytes()
        release = self.evolution_request(topic, operation="release-topic-gate", expected_revision=3, expected_topic_revision=1, release_set=evaluation["release_set"], release_set_sha256=evaluation["release_set_sha256"])
        release["actor_topic_id"] = dependent_id
        release["actor_conversation_ref"] = "codex-thread:dependent"
        code, rejected, _ = self.run_cli(release)
        self.assertEqual(code, 1)
        self.assertEqual(rejected["error"]["code"], "topic_gate_evaluation_stale")
        self.assertEqual(ledger.read_bytes(), before)

    def test_ticket07_stale_phase_result_release_is_rejected_via_cli(self) -> None:
        project = self.make_project("ticket07-stale-result-release", git=False)
        topic = self.bootstrap_topic(project)
        decision, revision, topic_revision = self.complete_update(project, topic, ledger_revision=1, topic_revision=1, mutation={"type": "confirm-decision", "summary": "Keep the API.", "rationale": "The result is authoritative."})
        completed, revision, topic_revision = self.complete_current_topic_phase(topic, ledger_revision=revision, topic_revision=topic_revision, from_phase=0, to_phase=1)
        ledger = Path(str(topic["ledger_path"]))
        frontmatter, records = PROTOCOL._load_records(ledger)
        dependent_id = "topic-" + "2" * 32
        add_topic(records, topic_id=dependent_id, root_slug="dependent", parent_topic_id=str(topic["topic_id"]), phase=1)
        add_binding(records, topic_id=dependent_id, conversation_ref="codex-thread:dependent")
        dependency_id = "DEP-" + "b" * 32
        add_closed_dependency(records, dependency_id=dependency_id, dependent_topic_id=dependent_id,
            prerequisite_topic_id=str(topic["topic_id"]), requirement_kind="phase-1-result",
            requirement_summary="The root Phase 1 result is current.", gate_reason_json=PROTOCOL._canonical_json({"kind": "explicit-create", "dependency_update_id": "00000000-0000-4000-8000-000000000000", "ledger_revision": 1}))
        ledger.write_bytes(PROTOCOL._render_records_ledger(frontmatter, records))
        gate = self.evolution_request(topic, operation="evaluate-topic-gate", basis_selection=[{"dependency_id": dependency_id, "authority_id": completed["phase_result_id"], "decision_ids": [decision["decision_id"]]}])
        gate["actor_topic_id"] = dependent_id
        gate["actor_conversation_ref"] = "codex-thread:dependent"
        code, evaluation, stderr = self.run_cli(gate)
        self.assertEqual(code, 0, stderr)
        _, records = PROTOCOL._load_records(ledger)
        result = next(item for item in records["Phase Results"] if item["result_id"] == completed["phase_result_id"])
        result["state"] = "superseded"
        ledger.write_bytes(PROTOCOL._render_records_ledger(frontmatter, records))
        before = ledger.read_bytes()
        release = self.evolution_request(topic, operation="release-topic-gate", expected_revision=revision, expected_topic_revision=1, release_set=evaluation["release_set"], release_set_sha256=evaluation["release_set_sha256"])
        release["actor_topic_id"] = dependent_id
        release["actor_conversation_ref"] = "codex-thread:dependent"
        code, rejected, _ = self.run_cli(release)
        self.assertEqual(code, 1)
        self.assertEqual(rejected["error"]["code"], "topic_gate_evaluation_stale")
        self.assertEqual(ledger.read_bytes(), before)

    def test_ticket07_v1_v2_dependency_migration_is_cli_stable(self) -> None:
        for version in ("1", "2"):
            with self.subTest(version=version):
                project = self.make_project(f"ticket07-v{version}-dependency-migration", git=False)
                topic = self.bootstrap_topic(project)
                ledger = Path(str(topic["ledger_path"]))
                frontmatter, records = PROTOCOL._load_records(ledger)
                frontmatter["schema_version"] = version
                if version == "1":
                    frontmatter.pop("creation_idempotency_key", None)
                    frontmatter.pop("creation_fingerprint", None)
                ledger.write_bytes(PROTOCOL._render_records_ledger(frontmatter, records))
                before_read = ledger.read_bytes()
                code, read, stderr = self.run_cli(self.evolution_request(topic, operation="read-topic"))
                self.assertEqual(code, 0, stderr)
                self.assertEqual(read["topic_dependencies"], [])
                self.assertEqual(ledger.read_bytes(), before_read)
                prepared = self.prepare_child_handoff(topic)
                self.assertEqual(len(prepared["initial_dependencies"]), 0)
                self.assertIn("schema_version: 3", ledger.read_text(encoding="utf-8"))

    def test_ticket07_gc_retains_active_dependency_checkpoint_basis_via_cli(self) -> None:
        project = self.make_project("ticket07-gc-dependency-retention", git=False)
        topic = self.bootstrap_topic(project)
        prepared = self.prepare_checkpoint(topic, ledger_revision=1, base_ref="project-root")
        code, published, stderr = self.run_cli(self.checkpoint_request(
            topic, operation="publish-non-git-checkpoint", ledger_revision=2,
            checkpoint_id=prepared["checkpoint_id"], expected_checkpoint_revision=1,
        ))
        self.assertEqual(code, 0, stderr)
        ledger = Path(str(topic["ledger_path"]))
        frontmatter, records = PROTOCOL._load_records(ledger)
        child_id = "topic-" + "d" * 32
        records["Current Topics"].append({"topic_id": child_id, "record_revision": 1, "root_slug": "child", "parent_topic_id": str(topic["topic_id"]), "current_phase": 1, "phase_state": "active", "review_state": "unreviewed", "topic_state": "open", "topic_document_path": None})
        checkpoint_record = records["Checkpoints"][0]
        checkpoint_data = json.loads(str(checkpoint_record["data_json"]))
        checkpoint_record["state"] = "cancelled"
        checkpoint_data["state"] = "cancelled"
        checkpoint_record["data_json"] = PROTOCOL._canonical_json(checkpoint_data)
        dependency_id = "DEP-" + "c" * 32
        basis = {"basis_version": 1, "dependency_id": dependency_id, "prerequisite_topic_id": str(topic["topic_id"]), "requirement_kind": "phase-0-checkpoint", "decision_authority": [], "authority": {"checkpoint_id": prepared["checkpoint_id"], "record_revision": 2, "published_identity": published["snapshot_digest"], "decision_digest": prepared["decision_digest"]}}
        dependency = {"dependency_id": dependency_id, "record_revision": 2, "dependent_topic_id": child_id, "prerequisite_topic_id": str(topic["topic_id"]), "requirement_kind": "phase-0-checkpoint", "requirement_summary": "Retain this checkpoint.", "relation_state": "active", "gate_state": "open", "accepted_basis_json": PROTOCOL._canonical_json(basis), "gate_reason_json": PROTOCOL._canonical_json({"kind": "atomic-release", "release_id": "00000000-0000-4000-8000-000000000000", "ledger_revision": 1})}
        records["Topic Dependencies"].append(dependency)
        ledger.write_bytes(PROTOCOL._render_records_ledger(frontmatter, records))
        code, retained, stderr = self.run_cli(self.checkpoint_request(topic, operation="checkpoint-gc-dry-run"))
        self.assertEqual(code, 0, stderr)
        self.assertEqual(retained["candidates"], [])
        dependency["relation_state"] = "cancelled"
        ledger.write_bytes(PROTOCOL._render_records_ledger(frontmatter, records))
        code, released, stderr = self.run_cli(self.checkpoint_request(topic, operation="checkpoint-gc-dry-run"))
        self.assertEqual(code, 0, stderr)
        self.assertEqual(released["candidates"], [{"digest": published["snapshot_digest"], "path": published["snapshot_path"]}])

    def test_ticket07_checkpoint_artifact_currentness_filters_and_rejects_via_cli(
        self,
    ) -> None:
        for fault in ("missing", "corrupt", "mismatched"):
            with self.subTest(fault=fault):
                project = self.make_project(f"ticket07-checkpoint-{fault}", git=False)
                topic = self.bootstrap_topic(project)
                checkpoint = self.publish_non_git_stage_entry_checkpoint(
                    topic, ledger_revision=1
                )
                ledger = Path(str(topic["ledger_path"]))
                frontmatter, records = PROTOCOL._load_records(ledger)
                dependent_id = "topic-" + uuid.uuid4().hex
                dependency_id = f"DEP-{uuid.uuid5(uuid.NAMESPACE_URL, f'checkpoint-{fault}').hex}"
                records["Current Topics"].append({"topic_id": dependent_id, "record_revision": 1, "root_slug": f"dependent-{fault}", "parent_topic_id": str(topic["topic_id"]), "current_phase": 0, "phase_state": "active", "review_state": "unreviewed", "topic_state": "open", "topic_document_path": None})
                records["Conversation Bindings"].append({"topic_id": dependent_id, "conversation_ref": "codex-thread:dependent", "binding_state": "active", "record_revision": 1, "handoff_id": None, "attempt_id": None})
                records["Topic Dependencies"].append({"dependency_id": dependency_id, "record_revision": 1, "dependent_topic_id": dependent_id, "prerequisite_topic_id": str(topic["topic_id"]), "requirement_kind": "phase-0-checkpoint", "requirement_summary": "The published checkpoint must remain exact.", "relation_state": "active", "gate_state": "closed", "accepted_basis_json": None, "gate_reason_json": PROTOCOL._canonical_json({"kind": "explicit-create", "dependency_update_id": "00000000-0000-4000-8000-000000000000", "ledger_revision": 1})})
                ledger.write_bytes(PROTOCOL._render_records_ledger(frontmatter, records))
                selected = self.evolution_request(topic, operation="evaluate-topic-gate", basis_selection=[{"dependency_id": dependency_id, "authority_id": checkpoint["checkpoint_id"], "decision_ids": []}])
                selected["actor_topic_id"] = dependent_id
                selected["actor_conversation_ref"] = "codex-thread:dependent"
                code, evaluation, stderr = self.run_cli(selected)
                self.assertEqual(code, 0, stderr)
                snapshot_path = Path(str(checkpoint["snapshot_path"]))
                if fault == "missing":
                    snapshot_path.unlink()
                elif fault == "corrupt":
                    snapshot_path.chmod(0o600)
                    snapshot_path.write_bytes(b"not a checkpoint artifact\n")
                else:
                    snapshot_path.chmod(0o600)
                    snapshot = json.loads(snapshot_path.read_text(encoding="utf-8"))
                    snapshot["purpose"] = "pause"
                    snapshot_path.write_text(
                        PROTOCOL._canonical_json(snapshot) + "\n", encoding="utf-8"
                    )
                filtered = self.evolution_request(topic, operation="evaluate-topic-gate")
                filtered["actor_topic_id"] = dependent_id
                filtered["actor_conversation_ref"] = "codex-thread:dependent"
                code, unavailable, stderr = self.run_cli(filtered)
                self.assertEqual(code, 0, stderr)
                self.assertEqual(unavailable["state"], "blocked")
                self.assertEqual(unavailable["dependencies"][0]["candidates"], [])
                before = ledger.read_bytes()
                release = self.evolution_request(topic, operation="release-topic-gate", expected_revision=3, expected_topic_revision=1, release_set=evaluation["release_set"], release_set_sha256=evaluation["release_set_sha256"])
                release["actor_topic_id"] = dependent_id
                release["actor_conversation_ref"] = "codex-thread:dependent"
                code, rejected, _ = self.run_cli(release)
                self.assertEqual(code, 1)
                self.assertEqual(
                    rejected["error"]["code"],
                    "topic_gate_evaluation_stale",
                )
                self.assertEqual(ledger.read_bytes(), before)

    def test_ticket07_phase_result_currentness_filters_and_rejects_via_cli(
        self,
    ) -> None:
        for fault in ("superseded", "frozen-digest-mismatch"):
            with self.subTest(fault=fault):
                project = self.make_project(f"ticket07-result-{fault}", git=False)
                topic = self.bootstrap_topic(project)
                decision, revision, topic_revision = self.complete_update(project, topic, ledger_revision=1, topic_revision=1, mutation={"type": "confirm-decision", "summary": "Keep the API.", "rationale": "It is frozen in the Phase Result."})
                completed, revision, _ = self.complete_current_topic_phase(topic, ledger_revision=revision, topic_revision=topic_revision, from_phase=0, to_phase=1)
                ledger = Path(str(topic["ledger_path"]))
                frontmatter, records = PROTOCOL._load_records(ledger)
                dependent_id = "topic-" + uuid.uuid4().hex
                dependency_id = f"DEP-{uuid.uuid5(uuid.NAMESPACE_URL, f'result-{fault}').hex}"
                records["Current Topics"].append({"topic_id": dependent_id, "record_revision": 1, "root_slug": f"dependent-{fault}", "parent_topic_id": str(topic["topic_id"]), "current_phase": 1, "phase_state": "active", "review_state": "unreviewed", "topic_state": "open", "topic_document_path": None})
                records["Conversation Bindings"].append({"topic_id": dependent_id, "conversation_ref": "codex-thread:dependent", "binding_state": "active", "record_revision": 1, "handoff_id": None, "attempt_id": None})
                records["Topic Dependencies"].append({"dependency_id": dependency_id, "record_revision": 1, "dependent_topic_id": dependent_id, "prerequisite_topic_id": str(topic["topic_id"]), "requirement_kind": "phase-1-result", "requirement_summary": "The frozen Phase Result remains current.", "relation_state": "active", "gate_state": "closed", "accepted_basis_json": None, "gate_reason_json": PROTOCOL._canonical_json({"kind": "explicit-create", "dependency_update_id": "00000000-0000-4000-8000-000000000000", "ledger_revision": 1})})
                ledger.write_bytes(PROTOCOL._render_records_ledger(frontmatter, records))
                selected = self.evolution_request(topic, operation="evaluate-topic-gate", basis_selection=[{"dependency_id": dependency_id, "authority_id": completed["phase_result_id"], "decision_ids": [decision["decision_id"]]}])
                selected["actor_topic_id"] = dependent_id
                selected["actor_conversation_ref"] = "codex-thread:dependent"
                code, evaluation, stderr = self.run_cli(selected)
                self.assertEqual(code, 0, stderr)
                _, records = PROTOCOL._load_records(ledger)
                result = next(item for item in records["Phase Results"] if item["result_id"] == completed["phase_result_id"])
                if fault == "superseded":
                    result["state"] = "superseded"
                else:
                    data = json.loads(str(result["data_json"]))
                    data["decision_authority"][0]["sha256"] = "0" * 64
                    result["data_json"] = PROTOCOL._canonical_json(data)
                ledger.write_bytes(PROTOCOL._render_records_ledger(frontmatter, records))
                filtered = self.evolution_request(topic, operation="evaluate-topic-gate")
                filtered["actor_topic_id"] = dependent_id
                filtered["actor_conversation_ref"] = "codex-thread:dependent"
                code, unavailable, stderr = self.run_cli(filtered)
                self.assertEqual(code, 0, stderr)
                self.assertEqual(unavailable["state"], "blocked")
                self.assertEqual(unavailable["dependencies"][0]["candidates"], [])
                before = ledger.read_bytes()
                release = self.evolution_request(topic, operation="release-topic-gate", expected_revision=revision, expected_topic_revision=1, release_set=evaluation["release_set"], release_set_sha256=evaluation["release_set_sha256"])
                release["actor_topic_id"] = dependent_id
                release["actor_conversation_ref"] = "codex-thread:dependent"
                code, rejected, _ = self.run_cli(release)
                self.assertEqual(code, 1)
                self.assertEqual(rejected["error"]["code"], "topic_gate_evaluation_stale")
                self.assertEqual(ledger.read_bytes(), before)

    def test_ticket07_gc_retains_frozen_child_checkpoint_authority_via_cli(
        self,
    ) -> None:
        project = self.make_project("ticket07-gc-frozen-child-authority", git=False)
        topic = self.bootstrap_topic(project)
        checkpoint = self.publish_non_git_stage_entry_checkpoint(topic, ledger_revision=1)
        ledger = Path(str(topic["ledger_path"]))
        frontmatter, records = PROTOCOL._load_records(ledger)
        checkpoint_record = records["Checkpoints"][0]
        checkpoint_data = json.loads(str(checkpoint_record["data_json"]))
        checkpoint_record["state"] = "cancelled"
        checkpoint_data["state"] = "cancelled"
        checkpoint_record["data_json"] = PROTOCOL._canonical_json(checkpoint_data)
        records["Phase Results"].append(
            {
                "result_id": "CR-frozen-checkpoint",
                "result_kind": "child-topic-result",
                "state": "pending",
                "record_revision": 1,
                "authority_json": PROTOCOL._canonical_json(
                    {
                        "authority_kind": "phase-0-checkpoint",
                        "authority_identity": checkpoint["checkpoint_id"],
                        "decision_ids": [],
                        "decision_authority": [],
                        "topic_phase": 0,
                        "authority": {
                            "checkpoint_id": checkpoint["checkpoint_id"],
                            "record_revision": checkpoint_data["record_revision"],
                            "published_identity": checkpoint["snapshot_digest"],
                            "decision_digest": checkpoint_data["decision_digest"],
                        },
                    }
                ),
            }
        )
        ledger.write_bytes(PROTOCOL._render_records_ledger(frontmatter, records))

        code, retained, stderr = self.run_cli(
            self.checkpoint_request(topic, operation="checkpoint-gc-dry-run")
        )

        self.assertEqual(code, 0, stderr)
        self.assertEqual(retained["candidates"], [])
        records["Phase Results"][0]["state"] = "absorbed"
        ledger.write_bytes(PROTOCOL._render_records_ledger(frontmatter, records))
        code, collectable, stderr = self.run_cli(
            self.checkpoint_request(topic, operation="checkpoint-gc-dry-run")
        )
        self.assertEqual(code, 0, stderr)
        self.assertEqual(
            [item["digest"] for item in collectable["candidates"]],
            [checkpoint["snapshot_digest"]],
        )

    def test_ticket07_gc_rejects_corrupt_live_child_authority_via_cli(self) -> None:
        project = self.make_project("ticket07-gc-corrupt-live-child-authority", git=False)
        topic = self.bootstrap_topic(project)
        checkpoint = self.publish_non_git_stage_entry_checkpoint(topic, ledger_revision=1)
        ledger = Path(str(topic["ledger_path"]))
        frontmatter, records = PROTOCOL._load_records(ledger)
        checkpoint_record = records["Checkpoints"][0]
        checkpoint_data = json.loads(str(checkpoint_record["data_json"]))
        checkpoint_record["state"] = "cancelled"
        checkpoint_data["state"] = "cancelled"
        checkpoint_record["data_json"] = PROTOCOL._canonical_json(checkpoint_data)
        records["Phase Results"].append(
            {
                "result_id": "CR-corrupt-frozen-checkpoint",
                "result_kind": "child-topic-result",
                "state": "pending",
                "record_revision": 1,
                "authority_json": "not-canonical-json",
            }
        )
        ledger.write_bytes(PROTOCOL._render_records_ledger(frontmatter, records))
        before = ledger.read_bytes()
        code, rejected, _ = self.run_cli(
            self.checkpoint_request(topic, operation="checkpoint-gc-dry-run")
        )
        self.assertEqual(code, 1)
        self.assertEqual(rejected["error"]["code"], "state_corrupt")
        self.assertEqual(ledger.read_bytes(), before)
        self.assertTrue(Path(str(checkpoint["snapshot_path"])).exists())

    def test_ticket07_broken_checkpoint_recloses_only_direct_gates_via_cli(self) -> None:
        project = self.make_project("ticket07-broken-checkpoint-direct-reclose", git=False)
        topic = self.bootstrap_topic(project)
        checkpoint = self.publish_non_git_stage_entry_checkpoint(topic, ledger_revision=1)
        ledger = Path(str(topic["ledger_path"]))
        frontmatter, records = PROTOCOL._load_records(ledger)
        dependent_id = "topic-" + uuid.uuid4().hex
        dependency_id = "DEP-" + uuid.uuid4().hex
        records["Current Topics"].append({"topic_id": dependent_id, "record_revision": 1, "root_slug": "dependent", "parent_topic_id": str(topic["topic_id"]), "current_phase": 0, "phase_state": "active", "review_state": "unreviewed", "topic_state": "open", "topic_document_path": None})
        records["Conversation Bindings"].append({"topic_id": dependent_id, "conversation_ref": "codex-thread:dependent", "binding_state": "active", "record_revision": 1, "handoff_id": None, "attempt_id": None})
        records["Topic Dependencies"].append({"dependency_id": dependency_id, "record_revision": 1, "dependent_topic_id": dependent_id, "prerequisite_topic_id": str(topic["topic_id"]), "requirement_kind": "phase-0-checkpoint", "requirement_summary": "The checkpoint remains publishable.", "relation_state": "active", "gate_state": "closed", "accepted_basis_json": None, "gate_reason_json": PROTOCOL._canonical_json({"kind": "explicit-create", "dependency_update_id": "00000000-0000-4000-8000-000000000000", "ledger_revision": 1})})
        ledger.write_bytes(PROTOCOL._render_records_ledger(frontmatter, records))
        evaluation_request = self.evolution_request(topic, operation="evaluate-topic-gate", basis_selection=[{"dependency_id": dependency_id, "authority_id": checkpoint["checkpoint_id"], "decision_ids": []}])
        evaluation_request["actor_topic_id"] = dependent_id
        evaluation_request["actor_conversation_ref"] = "codex-thread:dependent"
        code, evaluation, stderr = self.run_cli(evaluation_request)
        self.assertEqual(code, 0, stderr)
        release = self.evolution_request(topic, operation="release-topic-gate", expected_revision=3, expected_topic_revision=1, release_set=evaluation["release_set"], release_set_sha256=evaluation["release_set_sha256"])
        release["actor_topic_id"] = dependent_id
        release["actor_conversation_ref"] = "codex-thread:dependent"
        code, released, stderr = self.run_cli(release)
        self.assertEqual(code, 0, stderr)
        self.assertEqual(released["state"], "open")
        dependent_before = next(item.copy() for item in PROTOCOL._load_records(ledger)[1]["Current Topics"] if item["topic_id"] == dependent_id)
        broken_request = self.checkpoint_request(
            topic, operation="mark-checkpoint-broken", ledger_revision=4,
            checkpoint_id=checkpoint["checkpoint_id"], expected_checkpoint_revision=2,
            broken_identity=checkpoint["snapshot_digest"], reason="snapshot object disappeared",
        )
        code, broken, stderr = self.run_cli(broken_request)
        self.assertEqual(code, 0, stderr)
        self.assertEqual(broken["reclosed_dependency_ids"], [dependency_id])
        _, after = PROTOCOL._load_records(ledger)
        dependency = next(item for item in after["Topic Dependencies"] if item["dependency_id"] == dependency_id)
        reason = json.loads(str(dependency["gate_reason_json"]))
        self.assertEqual(dependency["gate_state"], "closed")
        self.assertEqual(reason["checkpoint_id"], checkpoint["checkpoint_id"])
        self.assertEqual(reason["checkpoint_broken_id"], broken_request["idempotency_key"])
        self.assertEqual(reason["broken_identity"], checkpoint["snapshot_digest"])
        self.assertEqual(next(item for item in after["Current Topics"] if item["topic_id"] == dependent_id), dependent_before)
        before_replay = ledger.read_bytes()
        code, replay, stderr = self.run_cli(broken_request)
        self.assertEqual(code, 0, stderr)
        self.assertTrue(replay["idempotent_replay"])
        self.assertEqual(ledger.read_bytes(), before_replay)

    def test_ticket07_git_checkpoint_authority_evaluates_and_stales_via_cli(self) -> None:
        project = self.make_project("ticket07-git-checkpoint-authority", git=True)
        (project / "base.txt").write_text("base\n", encoding="utf-8")
        subprocess.run(["git", "-C", str(project), "add", "base.txt"], check=True)
        subprocess.run(["git", "-C", str(project), "-c", "user.name=Test", "-c", "user.email=test@example.com", "commit", "-qm", "base"], check=True)
        topic = self.bootstrap_topic(project)
        prepared = self.prepare_checkpoint(topic, ledger_revision=1, purpose="stage-entry")
        checkpoint = self.publish_git_checkpoint(project, topic, prepared, ledger_revision=2)
        ledger = Path(str(topic["ledger_path"]))
        frontmatter, records = PROTOCOL._load_records(ledger)
        dependent_id = "topic-" + uuid.uuid4().hex
        dependency_id = "DEP-" + uuid.uuid4().hex
        records["Current Topics"].append({"topic_id": dependent_id, "record_revision": 1, "root_slug": "dependent", "parent_topic_id": str(topic["topic_id"]), "current_phase": 0, "phase_state": "active", "review_state": "unreviewed", "topic_state": "open", "topic_document_path": None})
        records["Conversation Bindings"].append({"topic_id": dependent_id, "conversation_ref": "codex-thread:dependent", "binding_state": "active", "record_revision": 1, "handoff_id": None, "attempt_id": None})
        records["Topic Dependencies"].append({"dependency_id": dependency_id, "record_revision": 1, "dependent_topic_id": dependent_id, "prerequisite_topic_id": str(topic["topic_id"]), "requirement_kind": "phase-0-checkpoint", "requirement_summary": "The Git checkpoint stays exact.", "relation_state": "active", "gate_state": "closed", "accepted_basis_json": None, "gate_reason_json": PROTOCOL._canonical_json({"kind": "explicit-create", "dependency_update_id": "00000000-0000-4000-8000-000000000000", "ledger_revision": 1})})
        ledger.write_bytes(PROTOCOL._render_records_ledger(frontmatter, records))
        gate = self.evolution_request(topic, operation="evaluate-topic-gate", basis_selection=[{"dependency_id": dependency_id, "authority_id": checkpoint["checkpoint_id"], "decision_ids": []}])
        gate["actor_topic_id"] = dependent_id
        gate["actor_conversation_ref"] = "codex-thread:dependent"
        code, evaluation, stderr = self.run_cli(gate)
        self.assertEqual(code, 0, stderr)
        self.assertEqual(evaluation["state"], "releasable")
        subprocess.run(["git", "-C", str(project), "update-ref", str(checkpoint["checkpoint_ref"]), "HEAD"], check=True)
        code, unavailable, _ = self.run_cli(gate)
        self.assertEqual(code, 1)
        self.assertEqual(unavailable["error"]["code"], "topic_dependency_evidence_unavailable")
        before = ledger.read_bytes()
        release = self.evolution_request(topic, operation="release-topic-gate", expected_revision=3, expected_topic_revision=1, release_set=evaluation["release_set"], release_set_sha256=evaluation["release_set_sha256"])
        release["actor_topic_id"] = dependent_id
        release["actor_conversation_ref"] = "codex-thread:dependent"
        code, rejected, _ = self.run_cli(release)
        self.assertEqual(code, 1)
        self.assertEqual(rejected["error"]["code"], "topic_gate_evaluation_stale")
        self.assertEqual(ledger.read_bytes(), before)

    def test_ticket07_repaired_git_checkpoint_is_current_authority_via_cli(self) -> None:
        project = self.make_project("ticket07-repaired-git-checkpoint", git=True)
        (project / "base.txt").write_text("base\n", encoding="utf-8")
        subprocess.run(["git", "-C", str(project), "add", "base.txt"], check=True)
        subprocess.run(["git", "-C", str(project), "-c", "user.name=Test", "-c", "user.email=test@example.com", "commit", "-qm", "base"], check=True)
        topic = self.bootstrap_topic(project)
        prepared = self.prepare_checkpoint(topic, ledger_revision=1, purpose="stage-entry")
        published = self.publish_git_checkpoint(project, topic, prepared, ledger_revision=2)
        original_commit = str(published["commit_id"])
        tree = subprocess.run(["git", "-C", str(project), "show", "-s", "--format=%T", "HEAD"], check=True, stdout=subprocess.PIPE, text=True).stdout.strip()
        replacement_parent = subprocess.run(
            ["git", "-C", str(project), "commit-tree", tree], input="rewritten base\n",
            check=True, stdout=subprocess.PIPE, text=True,
            env={**os.environ, "GIT_AUTHOR_NAME": "Test", "GIT_AUTHOR_EMAIL": "test@example.com", "GIT_COMMITTER_NAME": "Test", "GIT_COMMITTER_EMAIL": "test@example.com"},
        ).stdout.strip()
        replacement = self.create_matching_checkpoint_commit(
            project, prepared, timestamp="2026-02-01T00:00:00+00:00", parent_commit=replacement_parent,
        )
        code, _, stderr = self.run_cli(self.checkpoint_request(
            topic, operation="mark-checkpoint-broken", ledger_revision=3,
            checkpoint_id=prepared["checkpoint_id"], expected_checkpoint_revision=2,
            broken_identity=original_commit, reason="history rewritten",
        ))
        self.assertEqual(code, 0, stderr)
        repair = self.checkpoint_request(
            topic, operation="repair-checkpoint", ledger_revision=4,
            checkpoint_id=prepared["checkpoint_id"], expected_checkpoint_revision=3,
            replacement_commit=replacement, replacement_base_ref=replacement_parent,
        )
        code, repaired, stderr = self.run_cli(repair)
        self.assertEqual(code, 0, stderr)
        self.assertEqual(repaired["replacement_identity"], replacement)
        ledger = Path(str(topic["ledger_path"]))
        after_repair = ledger.read_bytes()
        code, replay, stderr = self.run_cli(repair)
        self.assertEqual(code, 0, stderr)
        self.assertTrue(replay["idempotent_replay"])
        self.assertEqual(ledger.read_bytes(), after_repair)
        frontmatter, records = PROTOCOL._load_records(ledger)
        dependent_id = "topic-" + uuid.uuid4().hex
        dependency_id = "DEP-" + uuid.uuid4().hex
        records["Current Topics"].append({"topic_id": dependent_id, "record_revision": 1, "root_slug": "dependent", "parent_topic_id": str(topic["topic_id"]), "current_phase": 0, "phase_state": "active", "review_state": "unreviewed", "topic_state": "open", "topic_document_path": None})
        records["Conversation Bindings"].append({"topic_id": dependent_id, "conversation_ref": "codex-thread:dependent", "binding_state": "active", "record_revision": 1, "handoff_id": None, "attempt_id": None})
        records["Topic Dependencies"].append({"dependency_id": dependency_id, "record_revision": 1, "dependent_topic_id": dependent_id, "prerequisite_topic_id": str(topic["topic_id"]), "requirement_kind": "phase-0-checkpoint", "requirement_summary": "The repaired checkpoint remains current.", "relation_state": "active", "gate_state": "closed", "accepted_basis_json": None, "gate_reason_json": PROTOCOL._canonical_json({"kind": "explicit-create", "dependency_update_id": "00000000-0000-4000-8000-000000000000", "ledger_revision": 1})})
        ledger.write_bytes(PROTOCOL._render_records_ledger(frontmatter, records))
        evaluate = self.evolution_request(topic, operation="evaluate-topic-gate", basis_selection=[{"dependency_id": dependency_id, "authority_id": prepared["checkpoint_id"], "decision_ids": []}])
        evaluate["actor_topic_id"] = dependent_id
        evaluate["actor_conversation_ref"] = "codex-thread:dependent"
        code, evaluation, stderr = self.run_cli(evaluate)
        self.assertEqual(code, 0, stderr)
        self.assertEqual(evaluation["state"], "releasable")
        release = self.evolution_request(topic, operation="release-topic-gate", expected_revision=5, expected_topic_revision=1, release_set=evaluation["release_set"], release_set_sha256=evaluation["release_set_sha256"])
        release["actor_topic_id"] = dependent_id
        release["actor_conversation_ref"] = "codex-thread:dependent"
        code, released, stderr = self.run_cli(release)
        self.assertEqual(code, 0, stderr)
        self.assertEqual(released["state"], "open")

    def test_ticket07_invalid_latest_checkpoint_does_not_fallback_via_cli(self) -> None:
        for fault in ("missing", "corrupt", "stale", "broken"):
            with self.subTest(fault=fault):
                project = self.make_project(f"ticket07-invalid-latest-{fault}", git=False)
                topic = self.bootstrap_topic(project)
                older = self.publish_non_git_stage_entry_checkpoint(topic, ledger_revision=1)
                latest = self.publish_non_git_stage_entry_checkpoint(topic, ledger_revision=3)
                ledger = Path(str(topic["ledger_path"]))
                frontmatter, records = PROTOCOL._load_records(ledger)
                dependent_id = "topic-" + uuid.uuid4().hex
                dependency_id = "DEP-" + uuid.uuid4().hex
                records["Current Topics"].append({"topic_id": dependent_id, "record_revision": 1, "root_slug": "dependent", "parent_topic_id": str(topic["topic_id"]), "current_phase": 0, "phase_state": "active", "review_state": "unreviewed", "topic_state": "open", "topic_document_path": None})
                records["Conversation Bindings"].append({"topic_id": dependent_id, "conversation_ref": "codex-thread:dependent", "binding_state": "active", "record_revision": 1, "handoff_id": None, "attempt_id": None})
                records["Topic Dependencies"].append({"dependency_id": dependency_id, "record_revision": 1, "dependent_topic_id": dependent_id, "prerequisite_topic_id": str(topic["topic_id"]), "requirement_kind": "phase-0-checkpoint", "requirement_summary": "Only the latest authority may release this gate.", "relation_state": "active", "gate_state": "closed", "accepted_basis_json": None, "gate_reason_json": PROTOCOL._canonical_json({"kind": "explicit-create", "dependency_update_id": "00000000-0000-4000-8000-000000000000", "ledger_revision": 1})})
                ledger.write_bytes(PROTOCOL._render_records_ledger(frontmatter, records))
                selected = self.evolution_request(topic, operation="evaluate-topic-gate", basis_selection=[{"dependency_id": dependency_id, "authority_id": latest["checkpoint_id"], "decision_ids": []}])
                selected["actor_topic_id"] = dependent_id
                selected["actor_conversation_ref"] = "codex-thread:dependent"
                code, evaluation, stderr = self.run_cli(selected)
                self.assertEqual(code, 0, stderr)
                self.assertEqual(evaluation["state"], "releasable")
                snapshot_path = Path(str(latest["snapshot_path"]))
                if fault == "missing":
                    snapshot_path.unlink()
                elif fault == "corrupt":
                    snapshot_path.chmod(0o600)
                    snapshot_path.write_bytes(b"not a checkpoint artifact\n")
                elif fault == "stale":
                    snapshot_path.chmod(0o600)
                    snapshot = json.loads(snapshot_path.read_text(encoding="utf-8"))
                    snapshot["purpose"] = "pause"
                    snapshot_path.write_text(PROTOCOL._canonical_json(snapshot) + "\n", encoding="utf-8")
                else:
                    broken = self.checkpoint_request(
                        topic, operation="mark-checkpoint-broken", ledger_revision=5,
                        checkpoint_id=latest["checkpoint_id"], expected_checkpoint_revision=2,
                        broken_identity=latest["snapshot_digest"], reason="latest authority was invalidated",
                    )
                    code, _, stderr = self.run_cli(broken)
                    self.assertEqual(code, 0, stderr)
                blocked = self.evolution_request(topic, operation="evaluate-topic-gate")
                blocked["actor_topic_id"] = dependent_id
                blocked["actor_conversation_ref"] = "codex-thread:dependent"
                code, unavailable, stderr = self.run_cli(blocked)
                self.assertEqual(code, 0, stderr)
                self.assertEqual(unavailable["state"], "blocked")
                self.assertEqual(unavailable["dependencies"][0]["candidates"], [])
                before = ledger.read_bytes()
                release = self.evolution_request(topic, operation="release-topic-gate", expected_revision=5, expected_topic_revision=1, release_set=evaluation["release_set"], release_set_sha256=evaluation["release_set_sha256"])
                release["actor_topic_id"] = dependent_id
                release["actor_conversation_ref"] = "codex-thread:dependent"
                code, rejected, _ = self.run_cli(release)
                self.assertEqual(code, 1)
                self.assertEqual(
                    rejected["error"]["code"],
                    "topic_gate_evaluation_stale",
                )
                self.assertEqual(ledger.read_bytes(), before)
                self.assertNotEqual(older["checkpoint_id"], latest["checkpoint_id"])

    def test_ticket07_bound_same_tree_child_uses_topic_and_checkpoint_cli(self) -> None:
        project = self.make_project("ticket07-bound-child-public-apis", git=False)
        parent = self.bootstrap_topic(project)
        handoff, child_ref = self.activate_child_handoff(parent)
        child = {**parent, "topic_id": handoff["target_topic_id"]}
        read = self.evolution_request(
            child, operation="read-topic", owner_ref=child_ref
        )
        code, observed, stderr = self.run_cli(read)
        self.assertEqual(code, 0, stderr)
        self.assertEqual(observed["record_revision"], 1)
        _, revision, topic_revision = self.complete_update(
            project, child, ledger_revision=5, topic_revision=1, owner_ref=child_ref,
            mutation={"type": "confirm-decision", "summary": "Keep the child API narrow.", "rationale": "It is owned by the bound child."},
        )
        prepare = self.checkpoint_request(
            child, operation="prepare-checkpoint", ledger_revision=revision,
            topic_revision=topic_revision, purpose="pause", base_ref="HEAD",
        )
        prepare["actor_conversation_ref"] = child_ref
        code, prepared, stderr = self.run_cli(prepare)
        self.assertEqual(code, 0, stderr)
        publish = self.checkpoint_request(
            child, operation="publish-non-git-checkpoint", ledger_revision=revision + 1,
            topic_revision=topic_revision, checkpoint_id=prepared["checkpoint_id"],
            expected_checkpoint_revision=prepared["checkpoint_record_revision"],
        )
        publish["actor_conversation_ref"] = child_ref
        code, published, stderr = self.run_cli(publish)
        self.assertEqual(code, 0, stderr)
        self.assertEqual(published["topic_id"], child["topic_id"])

    def test_ticket07_bound_child_prepares_nested_child_via_cli(self) -> None:
        project = self.make_project("ticket07-nested-child", git=False)
        root = self.bootstrap_topic(project)
        handoff, child_ref = self.activate_child_handoff(root)
        child = {**root, "topic_id": handoff["target_topic_id"]}
        request = self.handoff_request(
            child, operation="prepare-handoff", ledger_revision=5, owner_ref=child_ref,
            handoff_kind="child", target_slug="nested-api", scope=["nested"],
            work_snapshot={"goal": "Refine nested API.", "confirmed_decisions": [], "pending_questions": []},
            authoritative_references=[],
        )
        code, nested, stderr = self.run_cli(request)
        self.assertEqual(code, 0, stderr)
        self.assertEqual(nested["topic_id"], child["topic_id"])
        self.assertNotEqual(nested["target_topic_id"], child["topic_id"])
        ledger = Path(str(root["ledger_path"]))
        before = ledger.read_bytes()
        wrong_owner = self.handoff_request(
            child, operation="prepare-handoff", ledger_revision=6,
            owner_ref="codex-thread:wrong-owner", handoff_kind="child", target_slug="rejected",
            scope=["nested"], work_snapshot={"goal": "Reject.", "confirmed_decisions": [], "pending_questions": []}, authoritative_references=[],
        )
        code, rejected, _ = self.run_cli(wrong_owner)
        self.assertEqual(code, 1)
        self.assertEqual(rejected["error"]["code"], "document_ownership_conflict")
        self.assertEqual(ledger.read_bytes(), before)
