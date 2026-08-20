#!/usr/bin/env python3

from __future__ import annotations

import json
import hashlib
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile
import unittest
import uuid
from concurrent.futures import ThreadPoolExecutor


SCRIPT_PATH = Path(__file__).with_name("discussion_protocol.py")
SUPERVISION_SCRIPT_PATH = (
    Path(__file__).parents[2]
    / "guided-implementation"
    / "scripts"
    / "supervision_protocol.py"
)


class DiscussionProtocolBootstrapTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary_directory.name).resolve()

    def tearDown(self) -> None:
        self.temporary_directory.cleanup()

    def make_project(self, name: str, *, git: bool) -> Path:
        project = self.root / name
        project.mkdir()
        if git:
            subprocess.run(
                ["git", "init", "-q", str(project)],
                check=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
            )
        return project

    def request(
        self,
        project: Path,
        *,
        entry_mode: str = "explicit-skill",
        invocation_id: str | None = None,
        conversation_ref: str = "codex-thread:bootstrap-test",
    ) -> dict[str, object]:
        request: dict[str, object] = {
            "protocol_version": 1,
            "operation": "bootstrap",
            "project_path": str(project),
            "entry_mode": entry_mode,
        }
        if entry_mode != "ordinary-consultation":
            request.update(
                {
                    "conversation_ref": conversation_ref,
                    "idempotency_key": invocation_id or str(uuid.uuid4()),
                    "root_slug": "checkout-redesign",
                }
            )
        return request

    def run_cli(
        self,
        request: dict[str, object],
        *,
        failpoint: str | None = None,
    ) -> tuple[int, dict[str, object], str]:
        environment = dict(os.environ)
        if failpoint is not None:
            environment["CODEX_DISCUSSION_TEST_FAILPOINT"] = failpoint
        completed = subprocess.run(
            [sys.executable, str(SCRIPT_PATH)],
            input=json.dumps(request),
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            env=environment,
        )
        self.assertTrue(completed.stdout, completed.stderr)
        return completed.returncode, json.loads(completed.stdout), completed.stderr

    def git_common_dir(self, project: Path) -> Path:
        completed = subprocess.run(
            ["git", "rev-parse", "--path-format=absolute", "--git-common-dir"],
            cwd=project,
            check=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        return Path(completed.stdout.strip())

    def rewrite_ledger_with_valid_digest(self, ledger_path: Path, old: str, new: str) -> None:
        text = ledger_path.read_text(encoding="utf-8").replace(old, new)
        frontmatter, body = text[4:].split("\n---\n", 1)
        without_digest = "\n".join(
            line for line in frontmatter.splitlines() if not line.startswith("content_digest: ")
        ) + "\n"
        digest = hashlib.sha256((without_digest + body).encode("utf-8")).hexdigest()
        text = re.sub(r"content_digest: [0-9a-f]{64}", f"content_digest: {digest}", text)
        ledger_path.write_text(text, encoding="utf-8")

    def assert_initialized(self, project: Path, response: dict[str, object]) -> None:
        self.assertTrue(response["ok"])
        self.assertEqual(response["state"], "started")
        self.assertTrue(response["reread_verified"])
        self.assertEqual(response["ledger_revision"], 1)
        self.assertEqual(response["topic_revision"], 1)

        manifest = Path(str(response["project_manifest_path"]))
        topic = Path(str(response["topic_document_path"]))
        ledger = Path(str(response["ledger_path"]))
        self.assertTrue(manifest.is_file())
        self.assertTrue(topic.is_file())
        self.assertTrue(ledger.is_file())
        self.assertEqual(manifest.parent, project / "docs" / "discussions")
        self.assertEqual(topic.parent, project / "docs" / "discussions" / "checkout-redesign")

        manifest_text = manifest.read_text(encoding="utf-8")
        topic_text = topic.read_text(encoding="utf-8")
        ledger_text = ledger.read_text(encoding="utf-8")
        for identity in ("project_id", "tree_id", "topic_id"):
            value = str(response[identity])
            self.assertIn(value, manifest_text)
            self.assertIn(value, topic_text)
            self.assertIn(value, ledger_text)
        self.assertIn("codex-thread:bootstrap-test", ledger_text)
        self.assertIn("active", ledger_text)
        for heading in (
            "## Current Topics",
            "## Pending Items",
            "## Phase Results",
            "## Checkpoints",
            "## Phase Runs",
            "## Pending Document Writes",
            "## Impacts",
            "## Relations and Coverage",
            "## Dependencies and Active Implementations",
            "## Conversation Bindings",
            "## Recent Events",
        ):
            self.assertIn(heading, ledger_text)
        for section in (
            "## Confirmed Decisions",
            "## Candidate Solution",
            "## Tentative Assumptions",
            "## Facts",
            "## Pending Questions",
            "## Decision Evolution",
        ):
            self.assertIn(section, topic_text)

    def test_all_explicit_entry_modes_bootstrap_a_git_root(self) -> None:
        for entry_mode in (
            "explicit-skill",
            "explicit-interface-name",
            "explicit-sustained-design",
        ):
            with self.subTest(entry_mode=entry_mode):
                project = self.make_project(entry_mode, git=True)
                returncode, response, stderr = self.run_cli(
                    self.request(project, entry_mode=entry_mode)
                )
                self.assertEqual(returncode, 0, stderr)
                self.assert_initialized(project, response)
                self.assertTrue(
                    Path(str(response["ledger_path"])).is_relative_to(
                        self.git_common_dir(project) / "cc-switch" / "design-discussion" / "v1"
                    )
                )

    def test_non_git_root_uses_project_local_coordination_storage(self) -> None:
        project = self.make_project("non-git", git=False)
        returncode, response, stderr = self.run_cli(self.request(project))
        self.assertEqual(returncode, 0, stderr)
        self.assert_initialized(project, response)
        self.assertTrue(
            Path(str(response["ledger_path"])).is_relative_to(
                project / ".codex" / "design-discussion" / "v1"
            )
        )

    def test_duplicate_invocation_is_an_exact_idempotent_replay(self) -> None:
        project = self.make_project("duplicate", git=True)
        invocation_id = str(uuid.uuid4())
        request = self.request(project, invocation_id=invocation_id)
        first_code, first, first_stderr = self.run_cli(request)
        self.assertEqual(first_code, 0, first_stderr)
        ledger_path = Path(str(first["ledger_path"]))
        original_ledger = ledger_path.read_bytes()

        second_code, second, second_stderr = self.run_cli(request)
        self.assertEqual(second_code, 0, second_stderr)
        self.assertTrue(second["ok"])
        self.assertTrue(second["idempotent_replay"])
        self.assertFalse(second["created"])
        self.assertEqual(second["ledger_revision"], 1)
        self.assertEqual(second["event_count"], 1)
        self.assertEqual(first["topic_id"], second["topic_id"])
        self.assertEqual(original_ledger, ledger_path.read_bytes())

    def test_duplicate_invocation_rejects_a_tampered_binding(self) -> None:
        project = self.make_project("tampered-replay", git=True)
        invocation_id = str(uuid.uuid4())
        request = self.request(project, invocation_id=invocation_id)
        first_code, first, first_stderr = self.run_cli(request)
        self.assertEqual(first_code, 0, first_stderr)
        ledger_path = Path(str(first["ledger_path"]))
        ledger_text = ledger_path.read_text(encoding="utf-8")
        ledger_path.write_text(
            ledger_text.replace("binding_state: \"active\"", "binding_state: \"inactive\""),
            encoding="utf-8",
        )

        returncode, response, _ = self.run_cli(request)
        self.assertEqual(returncode, 1)
        self.assertFalse(response["ok"])
        self.assertEqual(response["error"]["code"], "state_corrupt")
        self.assertEqual(response["state"], "stopped")
        self.assertEqual(response["ledger_revision"], 1)
        self.assertEqual(response["topic_id"], first["topic_id"])

    def test_duplicate_invocation_rejects_semantically_changed_records_with_valid_digest(self) -> None:
        project = self.make_project("semantic-tamper", git=True)
        invocation_id = str(uuid.uuid4())
        request = self.request(project, invocation_id=invocation_id)
        first_code, first, first_stderr = self.run_cli(request)
        self.assertEqual(first_code, 0, first_stderr)
        ledger_path = Path(str(first["ledger_path"]))
        self.rewrite_ledger_with_valid_digest(
            ledger_path, "record_revision: 1", "record_revision: 2"
        )

        returncode, response, _ = self.run_cli(request)
        self.assertEqual(returncode, 1)
        self.assertFalse(response["ok"])
        self.assertEqual(response["error"]["code"], "state_corrupt")

    def test_duplicate_invocation_rejects_an_invalid_manifest_slug(self) -> None:
        project = self.make_project("invalid-replay-slug", git=True)
        invocation_id = str(uuid.uuid4())
        request = self.request(project, invocation_id=invocation_id)
        first_code, first, first_stderr = self.run_cli(request)
        self.assertEqual(first_code, 0, first_stderr)
        manifest_path = Path(str(first["project_manifest_path"]))
        manifest_text = manifest_path.read_text(encoding="utf-8")
        manifest_path.write_text(
            manifest_text.replace(
                "root_slug: checkout-redesign", "root_slug: ../../outside"
            ),
            encoding="utf-8",
        )

        returncode, response, _ = self.run_cli(request)
        self.assertEqual(returncode, 1)
        self.assertFalse(response["ok"])
        self.assertEqual(response["error"]["code"], "state_corrupt")
        self.assertFalse((self.root / "outside" / "topic.md").exists())

    def test_reused_idempotency_key_with_changed_binding_is_a_conflict(self) -> None:
        project = self.make_project("idempotency-conflict", git=True)
        invocation_id = str(uuid.uuid4())
        first_request = self.request(project, invocation_id=invocation_id)
        first_code, _, first_stderr = self.run_cli(first_request)
        self.assertEqual(first_code, 0, first_stderr)
        changed_request = self.request(
            project,
            invocation_id=invocation_id,
            conversation_ref="codex-thread:different-conversation",
        )

        returncode, response, _ = self.run_cli(changed_request)
        self.assertEqual(returncode, 1)
        self.assertFalse(response["ok"])
        self.assertEqual(response["error"]["code"], "idempotency_conflict")

    def test_skill_metadata_disables_implicit_invocation(self) -> None:
        metadata = SCRIPT_PATH.parents[1] / "agents" / "openai.yaml"
        self.assertIn(
            "allow_implicit_invocation: false", metadata.read_text(encoding="utf-8")
        )

    def test_error_response_includes_localized_contract_and_authoritative_state(self) -> None:
        project = self.make_project("error-contract", git=True)
        invocation_id = str(uuid.uuid4())
        request = self.request(project, invocation_id=invocation_id)
        first_code, first, first_stderr = self.run_cli(request)
        self.assertEqual(first_code, 0, first_stderr)
        ledger_path = Path(str(first["ledger_path"]))
        ledger_text = ledger_path.read_text(encoding="utf-8")
        ledger_path.write_text(
            re.sub(r"content_digest: [0-9a-f]{64}", "content_digest: " + "0" * 64, ledger_text),
            encoding="utf-8",
        )

        returncode, response, _ = self.run_cli(request)
        self.assertEqual(returncode, 1)
        error = response["error"]
        self.assertEqual(error["code"], "state_corrupt")
        self.assertTrue(error["message_zh"])
        self.assertEqual(response["state"], "stopped")
        self.assertEqual(response["ledger_revision"], 1)
        self.assertEqual(response["topic_id"], first["topic_id"])

    def test_late_initialization_failure_rolls_back_every_new_artifact(self) -> None:
        project = self.make_project("partial-failure", git=True)
        blocking_path = (
            project / "docs" / "discussions" / "checkout-redesign" / "topic.md"
        )
        blocking_path.parent.mkdir(parents=True)
        blocking_path.write_text("pre-existing blocker\n", encoding="utf-8")

        returncode, response, _ = self.run_cli(self.request(project))
        self.assertEqual(returncode, 1)
        self.assertFalse(response["ok"])
        self.assertEqual(response["error"]["code"], "initialization_conflict")
        self.assertEqual(blocking_path.read_text(encoding="utf-8"), "pre-existing blocker\n")
        coordination_root = (
            self.git_common_dir(project) / "cc-switch" / "design-discussion" / "v1"
        )
        self.assertFalse(any(coordination_root.rglob("ledger.md")) if coordination_root.exists() else False)
        self.assertFalse((project / "docs" / "discussions" / ".codex-project.md").exists())

    def test_invalid_paths_fail_without_creating_project_state(self) -> None:
        regular_file = self.root / "not-a-project"
        regular_file.write_text("data\n", encoding="utf-8")
        for project_path in ("relative/project", str(regular_file), str(self.root / "missing")):
            with self.subTest(project_path=project_path):
                request = self.request(self.root)
                request["project_path"] = project_path
                returncode, response, _ = self.run_cli(request)
                self.assertEqual(returncode, 1)
                self.assertFalse(response["ok"])
                self.assertEqual(response["error"]["code"], "invalid_project_path")
        self.assertEqual(regular_file.read_text(encoding="utf-8"), "data\n")

    def test_symlinked_document_boundary_is_rejected_without_writing_target(self) -> None:
        project = self.make_project("symlink-boundary", git=True)
        outside = self.root / "outside-documents"
        outside.mkdir()
        (project / "docs").symlink_to(outside, target_is_directory=True)

        returncode, response, _ = self.run_cli(self.request(project))
        self.assertEqual(returncode, 1)
        self.assertFalse(response["ok"])
        self.assertEqual(response["error"]["code"], "invalid_storage_path")
        self.assertEqual(list(outside.iterdir()), [])
        coordination_root = (
            self.git_common_dir(project) / "cc-switch" / "design-discussion" / "v1"
        )
        self.assertFalse(any(coordination_root.rglob("ledger.md")) if coordination_root.exists() else False)

    def test_ordinary_consultation_has_zero_side_effects(self) -> None:
        project = self.make_project("stateless", git=True)
        before = sorted(path.relative_to(project) for path in project.rglob("*"))
        returncode, response, stderr = self.run_cli(
            self.request(project, entry_mode="ordinary-consultation")
        )
        self.assertEqual(returncode, 0, stderr)
        self.assertTrue(response["ok"])
        self.assertEqual(response["state"], "stateless")
        self.assertFalse(response["created"])
        after = sorted(path.relative_to(project) for path in project.rglob("*"))
        self.assertEqual(before, after)
        self.assertFalse((project / "docs" / "discussions").exists())
        self.assertFalse(
            (self.git_common_dir(project) / "cc-switch" / "design-discussion").exists()
        )


class DiscussionProtocolEvolutionTests(DiscussionProtocolBootstrapTests):
    def bootstrap_topic(
        self, project: Path, *, owner_ref: str = "discussion-task"
    ) -> dict[str, object]:
        returncode, response, stderr = self.run_cli(
            self.request(project, conversation_ref=owner_ref)
        )
        self.assertEqual(returncode, 0, stderr)
        return response

    def phase_request(self, topic, operation, revision, **parameters):
        return {
            "protocol_version": 1,
            "operation": operation,
            "project_path": str(Path(str(topic["topic_document_path"])).parents[3]),
            "project_id": topic["project_id"],
            "tree_id": topic["tree_id"],
            "actor_topic_id": topic["topic_id"],
            "actor_conversation_ref": "discussion-task",
            "expected_ledger_revision": revision,
            "expected_topic_revision": parameters.pop("topic_revision", 1),
            "idempotency_key": str(uuid.uuid4()),
            **parameters,
        }

    def prepare_phase_run(
        self, topic, *, revision=1, from_phase=0, to_phase=1, carrier_kind="worker"
    ):
        code, prepared, stderr = self.run_cli(
            self.phase_request(
                topic,
                "prepare-phase-run",
                revision,
                from_phase=from_phase,
                to_phase=to_phase,
                route=f"{from_phase}->{to_phase}",
                carrier_kind=carrier_kind,
            )
        )
        self.assertEqual(code, 0, stderr)
        self.assertEqual(
            set(prepared["evidence"]),
            {"source", "route", "impact", "coverage", "dependency", "coordination"},
        )
        return prepared

    def publish_non_git_stage_entry_checkpoint(
        self,
        topic: dict[str, object],
        *,
        ledger_revision: int,
        topic_revision: int = 1,
    ) -> dict[str, object]:
        prepared = self.prepare_checkpoint(
            topic,
            ledger_revision=ledger_revision,
            topic_revision=topic_revision,
            purpose="stage-entry",
        )
        code, published, stderr = self.run_cli(
            self.checkpoint_request(
                topic,
                operation="publish-non-git-checkpoint",
                ledger_revision=ledger_revision + 1,
                topic_revision=topic_revision,
                checkpoint_id=prepared["checkpoint_id"],
                expected_checkpoint_revision=prepared["checkpoint_record_revision"],
            )
        )
        self.assertEqual(code, 0, stderr)
        return published

    def publish_non_git_checkpoint(
        self,
        topic: dict[str, object],
        *,
        ledger_revision: int,
        purpose: str,
        topic_revision: int = 1,
    ) -> dict[str, object]:
        prepared = self.prepare_checkpoint(
            topic,
            ledger_revision=ledger_revision,
            topic_revision=topic_revision,
            purpose=purpose,
        )
        code, published, stderr = self.run_cli(
            self.checkpoint_request(
                topic,
                operation="publish-non-git-checkpoint",
                ledger_revision=ledger_revision + 1,
                topic_revision=topic_revision,
                checkpoint_id=prepared["checkpoint_id"],
                expected_checkpoint_revision=prepared["checkpoint_record_revision"],
            )
        )
        self.assertEqual(code, 0, stderr)
        return published

    def complete_stage_one(self, topic: dict[str, object]) -> dict[str, object]:
        prepared = self.prepare_phase_run(
            topic,
            revision=1,
            from_phase=0,
            to_phase=1,
            carrier_kind="problem-framing",
        )
        evidence = prepared["evidence"]
        carrier = "discussion-task"
        self.assertEqual(
            self.run_cli(
                self.phase_request(
                    topic,
                    "authorize-phase-carrier",
                    2,
                    phase_run_id=prepared["phase_run_id"],
                    attempt_id=prepared["attempt_id"],
                    carrier_ref=carrier,
                )
            )[0],
            0,
        )
        self.assertEqual(
            self.run_cli(
                self.phase_request(
                    topic,
                    "phase-ready",
                    3,
                    phase_run_id=prepared["phase_run_id"],
                    attempt_id=prepared["attempt_id"],
                    carrier_ref=carrier,
                    evidence=evidence,
                )
            )[0],
            0,
        )
        self.assertEqual(
            self.run_cli(
                self.phase_request(
                    topic,
                    "phase-activate",
                    4,
                    phase_run_id=prepared["phase_run_id"],
                    attempt_id=prepared["attempt_id"],
                    evidence=evidence,
                )
            )[0],
            0,
        )
        self.assertEqual(
            self.run_cli(
                self.phase_request(
                    topic,
                    "claim-phase-completion",
                    5,
                    phase_run_id=prepared["phase_run_id"],
                    attempt_id=prepared["attempt_id"],
                    carrier_ref=carrier,
                    evidence=evidence,
                )
            )[0],
            0,
        )
        self.assertEqual(
            self.run_cli(
                self.phase_request(
                    topic,
                    "complete-phase-run",
                    6,
                    phase_run_id=prepared["phase_run_id"],
                    attempt_id=prepared["attempt_id"],
                    evidence=evidence,
                )
            )[0],
            0,
        )
        code, completed, stderr = self.run_cli(
            self.phase_request(
                topic,
                "finalize-phase-run",
                7,
                phase_run_id=prepared["phase_run_id"],
                attempt_id=prepared["attempt_id"],
                evidence=evidence,
            )
        )
        self.assertEqual(code, 0, stderr)
        return completed

    def authorize_continuous_flow(
        self,
        topic: dict[str, object],
        *,
        ledger_revision: int,
        checkpoint: dict[str, object],
        phase_result_id: str,
        user_reply: str = "执行后续全部流程",
    ) -> tuple[int, dict[str, object], str]:
        return self.run_cli(
            self.phase_request(
                topic,
                "authorize-continuous-flow",
                ledger_revision,
                topic_revision=2,
                source_checkpoint_id=checkpoint["checkpoint_id"],
                source_checkpoint_identity=checkpoint["snapshot_digest"],
                phase_result_id=phase_result_id,
                user_reply=user_reply,
            )
        )

    def wrapper_phase_request(
        self,
        topic: dict[str, object],
        revision: int,
        *,
        from_phase: int,
        to_phase: int,
        carrier_kind: str,
        source_checkpoint_id: str,
        flow_mode: str = "stepwise",
        flow_mode_source: str = "explicit-stage-confirmation",
        requirement_completeness: dict[str, bool] | None = None,
        scope: list[str] | None = None,
        topic_revision: int = 1,
    ) -> dict[str, object]:
        return self.phase_request(
            topic,
            "prepare-wrapper-phase-run",
            revision,
            topic_revision=topic_revision,
            from_phase=from_phase,
            to_phase=to_phase,
            route=f"{from_phase}->{to_phase}",
            carrier_kind=carrier_kind,
            source_checkpoint_id=source_checkpoint_id,
            flow_mode=flow_mode,
            flow_mode_source=flow_mode_source,
            requirement_completeness=requirement_completeness,
            scope=scope or ["repository"],
        )

    def test_wrapper_current_and_dedicated_problem_framing_share_topic_document(self) -> None:
        for carrier_kind, carrier_ref in (
            ("current-problem-framing", "discussion-task"),
            ("dedicated-grilling", "codex-thread:grilling"),
        ):
            project = self.make_project(f"wrapper-{carrier_kind}", git=False)
            topic = self.bootstrap_topic(project)
            checkpoint = self.publish_non_git_stage_entry_checkpoint(
                topic, ledger_revision=1
            )
            code, prepared, stderr = self.run_cli(
                self.wrapper_phase_request(
                    topic,
                    3,
                    from_phase=0,
                    to_phase=1,
                    carrier_kind=carrier_kind,
                    source_checkpoint_id=str(checkpoint["checkpoint_id"]),
                )
            )
            self.assertEqual(code, 0, stderr)
            self.assertEqual(prepared["topic_document_path"], topic["topic_document_path"])
            self.assertEqual(prepared["requirement_document_mode"], "shared-0-1-topic")
            _, _, _ = self.run_cli(
                self.phase_request(
                    topic,
                    "authorize-phase-carrier",
                    4,
                    phase_run_id=prepared["phase_run_id"],
                    attempt_id=prepared["attempt_id"],
                    carrier_ref=carrier_ref,
                )
            )
            claim = self.phase_request(
                topic,
                "claim-phase-carrier",
                5,
                phase_run_id=prepared["phase_run_id"],
                attempt_id=prepared["attempt_id"],
                carrier_ref=carrier_ref,
                source_checkpoint_id=checkpoint["checkpoint_id"],
                source_checkpoint_identity=checkpoint["snapshot_digest"],
            )
            claim["actor_conversation_ref"] = carrier_ref
            code, claimed, stderr = self.run_cli(claim)
            self.assertEqual(code, 0, stderr)
            self.assertTrue(claimed["attempt_claimed"])
            ready = self.phase_request(
                topic,
                "phase-ready",
                6,
                phase_run_id=prepared["phase_run_id"],
                attempt_id=prepared["attempt_id"],
                carrier_ref=carrier_ref,
                evidence=prepared["evidence"],
            )
            ready["actor_conversation_ref"] = carrier_ref
            code, _, stderr = self.run_cli(ready)
            self.assertEqual(code, 0, stderr)
            if carrier_kind == "dedicated-grilling":
                before_active = self.evolution_request(
                    topic,
                    operation="prepare-topic-update",
                    expected_revision=7,
                    expected_topic_revision=1,
                    owner_ref=carrier_ref,
                    mutation={
                        "type": "confirm-decision",
                        "summary": "Use the shared topic draft.",
                        "rationale": "The dedicated carrier must not create another draft.",
                    },
                )
                code, rejected, _ = self.run_cli(before_active)
                self.assertEqual(code, 1)
                self.assertEqual(rejected["error"]["code"], "document_ownership_conflict")
            code, _, stderr = self.run_cli(
                self.phase_request(
                    topic,
                    "phase-activate",
                    7,
                    phase_run_id=prepared["phase_run_id"],
                    attempt_id=prepared["attempt_id"],
                    evidence=prepared["evidence"],
                )
            )
            self.assertEqual(code, 0, stderr)
            if carrier_kind == "dedicated-grilling":
                forbidden_checkpoint = self.checkpoint_request(
                    topic,
                    operation="prepare-checkpoint",
                    ledger_revision=8,
                    owner_ref=carrier_ref,
                    purpose="stage-entry",
                    base_ref="HEAD",
                )
                code, rejected, _ = self.run_cli(forbidden_checkpoint)
                self.assertEqual(code, 1)
                self.assertEqual(rejected["error"]["code"], "document_ownership_conflict")
                code, update, stderr = self.run_cli(
                    self.evolution_request(
                        topic,
                        operation="prepare-topic-update",
                        expected_revision=8,
                        expected_topic_revision=1,
                        owner_ref=carrier_ref,
                        mutation={
                            "type": "confirm-decision",
                            "summary": "Use the shared topic draft.",
                            "rationale": "The dedicated carrier owns only the activated shared draft.",
                        },
                    )
                )
                self.assertEqual(code, 0, stderr)
                self.assertEqual(update["payload_path"].endswith(".payload"), True)
                self.assertEqual(
                    Path(str(topic["topic_document_path"])).name,
                    "topic.md",
                )

    def test_solution_wrapper_requires_claim_ready_activation_and_current_source(self) -> None:
        project = self.make_project("wrapper-solution", git=False)
        topic = self.bootstrap_topic(project)
        checkpoint = self.publish_non_git_stage_entry_checkpoint(topic, ledger_revision=1)
        code, prepared, stderr = self.run_cli(
            self.wrapper_phase_request(
                topic,
                3,
                from_phase=0,
                to_phase=2,
                carrier_kind="solution-designer",
                source_checkpoint_id=str(checkpoint["checkpoint_id"]),
            )
        )
        self.assertEqual(code, 0, stderr)
        self.assertEqual(
            prepared["stage_ownership"],
            ["spec", "adr", "tickets", "planning-commit"],
        )
        self.assertFalse(prepared["may_modify_requirement_source"])
        _, _, _ = self.run_cli(
            self.phase_request(
                topic,
                "authorize-phase-carrier",
                4,
                phase_run_id=prepared["phase_run_id"],
                attempt_id=prepared["attempt_id"],
                carrier_ref="agent:solution-designer",
            )
        )
        unclaimed_ready = self.phase_request(
            topic,
            "phase-ready",
            5,
            phase_run_id=prepared["phase_run_id"],
            attempt_id=prepared["attempt_id"],
            carrier_ref="agent:solution-designer",
            evidence=prepared["evidence"],
        )
        unclaimed_ready["actor_conversation_ref"] = "agent:solution-designer"
        code, rejected, _ = self.run_cli(unclaimed_ready)
        self.assertEqual(code, 1)
        self.assertEqual(rejected["error"]["code"], "phase_carrier_claim_required")
        claim = self.phase_request(
            topic,
            "claim-phase-carrier",
            5,
            phase_run_id=prepared["phase_run_id"],
            attempt_id=prepared["attempt_id"],
            carrier_ref="agent:solution-designer",
            source_checkpoint_id=checkpoint["checkpoint_id"],
            source_checkpoint_identity=checkpoint["snapshot_digest"],
        )
        claim["actor_conversation_ref"] = "agent:solution-designer"
        code, _, stderr = self.run_cli(claim)
        self.assertEqual(code, 0, stderr)
        ready = dict(unclaimed_ready)
        ready["idempotency_key"] = str(uuid.uuid4())
        ready["expected_ledger_revision"] = 6
        code, result, stderr = self.run_cli(ready)
        self.assertEqual(code, 0, stderr)
        self.assertEqual(result["state"], "ready")
        topic_path = Path(str(topic["topic_document_path"]))
        topic_path.write_text(topic_path.read_text(encoding="utf-8") + "\nsource drift\n", encoding="utf-8")
        code, drifted, _ = self.run_cli(
            self.phase_request(
                topic,
                "phase-activate",
                7,
                phase_run_id=prepared["phase_run_id"],
                attempt_id=prepared["attempt_id"],
                evidence=prepared["evidence"],
            )
        )
        self.assertEqual(code, 1)
        self.assertEqual(drifted["error"]["code"], "phase_source_drift")

    def test_wrapper_pending_impacts_and_direct_to_three_completeness(self) -> None:
        impacted_project = self.make_project("wrapper-pending-impact", git=False)
        impacted = self.bootstrap_topic(impacted_project)
        ledger = Path(str(impacted["ledger_path"]))
        marker = "## Impacts\n\n```yaml\nrecords:\n  []\n```"
        replacement = (
            "## Impacts\n\n```yaml\nrecords:\n"
            "  - impact_id: \"IMP-pending\"\n"
            f"    topic_id: \"{impacted['topic_id']}\"\n"
            '    data_json: "{\\"action\\":null,\\"decision_id\\":\\"D-pending\\",'
            '\\"direction\\":\\"changed\\",\\"impact_id\\":\\"IMP-pending\\",'
            '\\"state\\":\\"pending\\"}"\n```'
        )
        self.rewrite_ledger_with_valid_digest(ledger, marker, replacement)
        checkpoint = self.publish_non_git_stage_entry_checkpoint(impacted, ledger_revision=1)
        code, rejected, _ = self.run_cli(
            self.wrapper_phase_request(
                impacted,
                3,
                from_phase=0,
                to_phase=2,
                carrier_kind="solution-designer",
                source_checkpoint_id=str(checkpoint["checkpoint_id"]),
            )
        )
        self.assertEqual(code, 1)
        self.assertEqual(rejected["error"]["code"], "phase_impact_drift")

        project = self.make_project("wrapper-direct-three", git=False)
        topic = self.bootstrap_topic(project)
        phase_ledger = Path(str(topic["ledger_path"]))
        self.rewrite_ledger_with_valid_digest(
            phase_ledger, "current_phase: 0", "current_phase: 1"
        )
        checkpoint = self.publish_non_git_stage_entry_checkpoint(topic, ledger_revision=1)
        incomplete = {
            "scope": True,
            "behavior": True,
            "failures": True,
            "acceptance_conditions": True,
            "test_seam": False,
        }
        code, rejected, _ = self.run_cli(
            self.wrapper_phase_request(
                topic,
                3,
                from_phase=1,
                to_phase=3,
                carrier_kind="guided-implementation",
                source_checkpoint_id=str(checkpoint["checkpoint_id"]),
                requirement_completeness=incomplete,
                scope=["skills/design-discussion/scripts"],
            )
        )
        self.assertEqual(code, 1)
        self.assertEqual(rejected["error"]["code"], "phase_requirement_incomplete")
        complete = {key: True for key in incomplete}
        code, prepared, stderr = self.run_cli(
            self.wrapper_phase_request(
                topic,
                3,
                from_phase=1,
                to_phase=3,
                carrier_kind="guided-implementation",
                source_checkpoint_id=str(checkpoint["checkpoint_id"]),
                requirement_completeness=complete,
                scope=["skills/design-discussion/scripts"],
            )
        )
        self.assertEqual(code, 0, stderr)
        evidence = prepared["evidence"]
        _, _, _ = self.run_cli(
            self.phase_request(topic, "authorize-phase-carrier", 4, phase_run_id=prepared["phase_run_id"], attempt_id=prepared["attempt_id"], carrier_ref="agent:implementation")
        )
        claim = self.phase_request(topic, "claim-phase-carrier", 5, phase_run_id=prepared["phase_run_id"], attempt_id=prepared["attempt_id"], carrier_ref="agent:implementation", source_checkpoint_id=checkpoint["checkpoint_id"], source_checkpoint_identity=checkpoint["snapshot_digest"])
        claim["actor_conversation_ref"] = "agent:implementation"
        self.assertEqual(self.run_cli(claim)[0], 0)
        ready = self.phase_request(topic, "phase-ready", 6, phase_run_id=prepared["phase_run_id"], attempt_id=prepared["attempt_id"], carrier_ref="agent:implementation", evidence=evidence)
        ready["actor_conversation_ref"] = "agent:implementation"
        self.assertEqual(self.run_cli(ready)[0], 0)
        code, activated, stderr = self.run_cli(self.phase_request(topic, "phase-activate", 7, phase_run_id=prepared["phase_run_id"], attempt_id=prepared["attempt_id"], evidence=evidence))
        self.assertEqual(code, 0, stderr)
        self.assertEqual(activated["not_applicable_phase"], 2)
        self.assertEqual(activated["not_applicable_scope"], ["skills/design-discussion/scripts"])
        activated_ledger = phase_ledger.read_text(encoding="utf-8")
        self.assertIn('state: "not_applicable"', activated_ledger)
        self.assertNotIn("planning_artifact", activated_ledger)
        completion = self.phase_request(topic, "claim-phase-completion", 8, phase_run_id=prepared["phase_run_id"], attempt_id=prepared["attempt_id"], carrier_ref="agent:implementation", evidence=evidence)
        completion["actor_conversation_ref"] = "agent:implementation"
        self.assertEqual(self.run_cli(completion)[0], 0)
        self.assertEqual(self.run_cli(self.phase_request(topic, "complete-phase-run", 9, phase_run_id=prepared["phase_run_id"], attempt_id=prepared["attempt_id"], evidence=evidence))[0], 0)
        code, finalized, stderr = self.run_cli(self.phase_request(topic, "finalize-phase-run", 10, phase_run_id=prepared["phase_run_id"], attempt_id=prepared["attempt_id"], evidence=evidence))
        self.assertEqual(code, 0, stderr)
        ledger_text = phase_ledger.read_text(encoding="utf-8")
        self.assertIn('state: "not_applicable"', ledger_text)
        self.assertNotIn("planning_artifact", ledger_text)

    def test_wrapper_continuous_mode_only_inherits_successful_stage_one_footer(self) -> None:
        project = self.make_project("wrapper-continuous-zero", git=False)
        topic = self.bootstrap_topic(project)
        checkpoint = self.publish_non_git_stage_entry_checkpoint(topic, ledger_revision=1)
        code, rejected, _ = self.run_cli(
            self.wrapper_phase_request(
                topic,
                3,
                from_phase=0,
                to_phase=2,
                carrier_kind="solution-designer",
                source_checkpoint_id=str(checkpoint["checkpoint_id"]),
                flow_mode="continuous",
                flow_mode_source="successful-stage-1-footer",
            )
        )
        self.assertEqual(code, 1)
        self.assertEqual(rejected["error"]["code"], "phase_flow_mode_invalid")

        stage_one_project = self.make_project("wrapper-continuous-one", git=False)
        stage_one = self.bootstrap_topic(stage_one_project)
        stage_one_result = self.complete_stage_one(stage_one)
        stage_one_checkpoint = self.publish_non_git_stage_entry_checkpoint(
            stage_one, ledger_revision=8, topic_revision=2
        )
        self.assertIsNotNone(stage_one_checkpoint["stage_entry_phase_result_id"])
        code, rejected, _ = self.run_cli(
            self.wrapper_phase_request(
                stage_one,
                10,
                from_phase=1,
                to_phase=2,
                carrier_kind="solution-designer",
                source_checkpoint_id=str(stage_one_checkpoint["checkpoint_id"]),
                flow_mode="continuous",
                flow_mode_source="successful-stage-1-footer",
                topic_revision=2,
            )
        )
        self.assertEqual(code, 1)
        self.assertEqual(rejected["error"]["code"], "phase_flow_mode_invalid")

        code, rejected, _ = self.authorize_continuous_flow(
            stage_one,
            ledger_revision=10,
            checkpoint=stage_one_checkpoint,
            phase_result_id=str(stage_one_result["phase_result_id"]),
            user_reply="执行后续全部流程。",
        )
        self.assertEqual(code, 1)
        self.assertEqual(rejected["error"]["code"], "phase_flow_mode_invalid")
        code, authorized, stderr = self.authorize_continuous_flow(
            stage_one,
            ledger_revision=10,
            checkpoint=stage_one_checkpoint,
            phase_result_id=str(stage_one_result["phase_result_id"]),
        )
        self.assertEqual(code, 0, stderr)
        self.assertEqual(authorized["state"], "authorized")
        foreign = self.phase_request(
            stage_one,
            "authorize-continuous-flow",
            11,
            topic_revision=2,
            source_checkpoint_id=stage_one_checkpoint["checkpoint_id"],
            source_checkpoint_identity=stage_one_checkpoint["snapshot_digest"],
            phase_result_id=stage_one_result["phase_result_id"],
            user_reply="执行后续全部流程",
        )
        foreign["actor_conversation_ref"] = "codex-thread:not-source-owner"
        code, rejected, _ = self.run_cli(foreign)
        self.assertEqual(code, 1)
        self.assertEqual(rejected["error"]["code"], "document_ownership_conflict")
        code, continuous, stderr = self.run_cli(
            self.wrapper_phase_request(
                stage_one,
                11,
                from_phase=1,
                to_phase=2,
                carrier_kind="solution-designer",
                source_checkpoint_id=str(stage_one_checkpoint["checkpoint_id"]),
                flow_mode="continuous",
                flow_mode_source="successful-stage-1-footer",
                topic_revision=2,
            )
        )
        self.assertEqual(code, 0, stderr)
        self.assertEqual(continuous["flow_mode"], "continuous")
        self.assertEqual(
            continuous["continuous_authorization_id"],
            authorized["continuous_authorization_id"],
        )

        forged_project = self.make_project("wrapper-continuous-forged", git=False)
        forged = self.bootstrap_topic(forged_project)
        forged_ledger = Path(str(forged["ledger_path"]))
        self.rewrite_ledger_with_valid_digest(
            forged_ledger, "current_phase: 0", "current_phase: 1"
        )
        forged_checkpoint = self.publish_non_git_stage_entry_checkpoint(
            forged, ledger_revision=1
        )
        self.assertIsNone(forged_checkpoint["stage_entry_phase_result_id"])
        code, rejected, _ = self.run_cli(
            self.wrapper_phase_request(
                forged,
                3,
                from_phase=1,
                to_phase=2,
                carrier_kind="solution-designer",
                source_checkpoint_id=str(forged_checkpoint["checkpoint_id"]),
                flow_mode="continuous",
                flow_mode_source="successful-stage-1-footer",
            )
        )
        self.assertEqual(code, 1)
        self.assertEqual(rejected["error"]["code"], "phase_flow_mode_invalid")

        legacy = self.make_project("wrapper-no-context", git=False)
        before = sorted(legacy.rglob("*"))
        code, located, stderr = self.run_cli(
            {
                "protocol_version": 1,
                "operation": "discover-context",
                "project_path": str(legacy),
            }
        )
        self.assertEqual(code, 0, stderr)
        self.assertEqual(located["context"], "none")
        self.assertEqual(before, sorted(legacy.rglob("*")))

        ambiguous_root = legacy / "docs" / "discussions"
        for number in (1, 2):
            document = ambiguous_root / f"candidate-{number}" / "topic.md"
            document.parent.mkdir(parents=True, exist_ok=True)
            document.write_text(
                "---\n"
                f"project_id: project-{'1' * 32}\n"
                f"tree_id: tree-{'2' * 32}\n"
                f"topic_id: topic-{str(number) * 32}\n"
                "---\n",
                encoding="utf-8",
            )
        ambiguous_before = {
            path: path.read_bytes() for path in legacy.rglob("*") if path.is_file()
        }
        code, ambiguous, stderr = self.run_cli(
            {
                "protocol_version": 1,
                "operation": "discover-context",
                "project_path": str(legacy),
            }
        )
        self.assertEqual(code, 0, stderr)
        self.assertEqual(ambiguous["context"], "ambiguous")
        self.assertEqual(
            ambiguous_before,
            {path: path.read_bytes() for path in legacy.rglob("*") if path.is_file()},
        )

    def test_wrapper_rejects_superseded_checkpoint_and_wrong_carrier_route(self) -> None:
        project = self.make_project("wrapper-latest-checkpoint", git=False)
        topic = self.bootstrap_topic(project)
        first = self.publish_non_git_stage_entry_checkpoint(topic, ledger_revision=1)
        second = self.publish_non_git_stage_entry_checkpoint(topic, ledger_revision=3)
        code, rejected, _ = self.run_cli(
            self.wrapper_phase_request(
                topic,
                5,
                from_phase=0,
                to_phase=2,
                carrier_kind="solution-designer",
                source_checkpoint_id=str(first["checkpoint_id"]),
            )
        )
        self.assertEqual(code, 1)
        self.assertEqual(rejected["error"]["code"], "phase_checkpoint_invalid")
        wrong = self.wrapper_phase_request(
            topic,
            5,
            from_phase=0,
            to_phase=2,
            carrier_kind="dedicated-grilling",
            source_checkpoint_id=str(second["checkpoint_id"]),
        )
        code, rejected, _ = self.run_cli(wrong)
        self.assertEqual(code, 1)
        self.assertEqual(rejected["error"]["code"], "phase_route_conflict")

    def test_wrapper_accepts_only_latest_stage_entry_checkpoint(self) -> None:
        wrong_project = self.make_project("wrapper-wrong-purpose", git=False)
        wrong_topic = self.bootstrap_topic(wrong_project)
        implementation_source = self.publish_non_git_checkpoint(
            wrong_topic,
            ledger_revision=1,
            purpose="implementation-source",
        )
        code, rejected, _ = self.run_cli(
            self.wrapper_phase_request(
                wrong_topic,
                3,
                from_phase=0,
                to_phase=2,
                carrier_kind="solution-designer",
                source_checkpoint_id=str(implementation_source["checkpoint_id"]),
            )
        )
        self.assertEqual(code, 1)
        self.assertEqual(rejected["error"]["code"], "phase_checkpoint_invalid")

        current_project = self.make_project("wrapper-cross-purpose-recency", git=False)
        current_topic = self.bootstrap_topic(current_project)
        stage_entry = self.publish_non_git_stage_entry_checkpoint(
            current_topic, ledger_revision=1
        )
        later_implementation = self.publish_non_git_checkpoint(
            current_topic,
            ledger_revision=3,
            purpose="implementation-source",
        )
        code, prepared, stderr = self.run_cli(
            self.wrapper_phase_request(
                current_topic,
                5,
                from_phase=0,
                to_phase=2,
                carrier_kind="solution-designer",
                source_checkpoint_id=str(stage_entry["checkpoint_id"]),
            )
        )
        self.assertEqual(code, 0, stderr)
        self.assertEqual(prepared["source_checkpoint_id"], stage_entry["checkpoint_id"])
        self.assertNotEqual(
            prepared["source_checkpoint_id"], later_implementation["checkpoint_id"]
        )

        self.assertEqual(
            self.run_cli(
                self.phase_request(
                    current_topic,
                    "authorize-phase-carrier",
                    6,
                    phase_run_id=prepared["phase_run_id"],
                    attempt_id=prepared["attempt_id"],
                    carrier_ref="agent:solution-designer",
                )
            )[0],
            0,
        )
        claim = self.phase_request(
            current_topic,
            "claim-phase-carrier",
            7,
            phase_run_id=prepared["phase_run_id"],
            attempt_id=prepared["attempt_id"],
            carrier_ref="agent:solution-designer",
            source_checkpoint_id=later_implementation["checkpoint_id"],
            source_checkpoint_identity=later_implementation["snapshot_digest"],
        )
        claim["actor_conversation_ref"] = "agent:solution-designer"
        code, rejected, _ = self.run_cli(claim)
        self.assertEqual(code, 1)
        self.assertEqual(rejected["error"]["code"], "phase_checkpoint_invalid")

    def test_wrapper_activation_rechecks_latest_checkpoint(self) -> None:
        project = self.make_project("wrapper-activation-checkpoint", git=False)
        topic = self.bootstrap_topic(project)
        first = self.publish_non_git_stage_entry_checkpoint(topic, ledger_revision=1)
        code, prepared, stderr = self.run_cli(
            self.wrapper_phase_request(
                topic,
                3,
                from_phase=0,
                to_phase=2,
                carrier_kind="solution-designer",
                source_checkpoint_id=str(first["checkpoint_id"]),
            )
        )
        self.assertEqual(code, 0, stderr)
        _, _, _ = self.run_cli(
            self.phase_request(topic, "authorize-phase-carrier", 4, phase_run_id=prepared["phase_run_id"], attempt_id=prepared["attempt_id"], carrier_ref="agent:solution-designer")
        )
        claim = self.phase_request(topic, "claim-phase-carrier", 5, phase_run_id=prepared["phase_run_id"], attempt_id=prepared["attempt_id"], carrier_ref="agent:solution-designer", source_checkpoint_id=first["checkpoint_id"], source_checkpoint_identity=first["snapshot_digest"])
        claim["actor_conversation_ref"] = "agent:solution-designer"
        self.assertEqual(self.run_cli(claim)[0], 0)
        ready = self.phase_request(topic, "phase-ready", 6, phase_run_id=prepared["phase_run_id"], attempt_id=prepared["attempt_id"], carrier_ref="agent:solution-designer", evidence=prepared["evidence"])
        ready["actor_conversation_ref"] = "agent:solution-designer"
        self.assertEqual(self.run_cli(ready)[0], 0)
        self.publish_non_git_stage_entry_checkpoint(topic, ledger_revision=7)
        code, rejected, _ = self.run_cli(
            self.phase_request(topic, "phase-activate", 9, phase_run_id=prepared["phase_run_id"], attempt_id=prepared["attempt_id"], evidence=prepared["evidence"])
        )
        self.assertEqual(code, 1)
        self.assertEqual(rejected["error"]["code"], "phase_checkpoint_invalid")

    def test_phase_run_requires_owner_and_uses_single_revision_events(self) -> None:
        project = self.make_project("phase-run", git=False)
        topic = self.bootstrap_topic(project)
        prepared = self.prepare_phase_run(topic, carrier_kind="problem-framing")
        evidence = prepared["evidence"]
        _, authorized, _ = self.run_cli(self.phase_request(topic, "authorize-phase-carrier", 2, phase_run_id=prepared["phase_run_id"], attempt_id=prepared["attempt_id"], carrier_ref="untrusted"))
        read_request = self.phase_request(
            topic,
            "read-phase-run",
            0,
            phase_run_id=prepared["phase_run_id"],
        )
        for key in ("expected_ledger_revision", "expected_topic_revision", "idempotency_key"):
            read_request.pop(key)
        _, setup, _ = self.run_cli(read_request)
        self.assertEqual(setup["phase_run"]["state"], "setup-pending")
        request = self.phase_request(topic, "phase-ready", 3, phase_run_id=prepared["phase_run_id"], attempt_id=prepared["attempt_id"], carrier_ref="untrusted", evidence=evidence)
        request["actor_conversation_ref"] = "untrusted"
        request["carrier_ref"] = "untrusted"
        code, rejected, _ = self.run_cli(request)
        self.assertEqual(code, 0)
        code, active, stderr = self.run_cli(self.phase_request(topic, "phase-activate", 4, phase_run_id=prepared["phase_run_id"], attempt_id=prepared["attempt_id"], evidence=evidence))
        self.assertEqual(code, 0, stderr)
        claim = self.phase_request(topic, "claim-phase-completion", 5, phase_run_id=prepared["phase_run_id"], attempt_id=prepared["attempt_id"], carrier_ref="untrusted", evidence=evidence)
        claim["actor_conversation_ref"] = "untrusted"
        code, claimed, stderr = self.run_cli(claim)
        self.assertEqual(code, 0, stderr)
        code, pending, stderr = self.run_cli(self.phase_request(topic, "complete-phase-run", 6, phase_run_id=prepared["phase_run_id"], attempt_id=prepared["attempt_id"], evidence=evidence))
        self.assertEqual(code, 0, stderr)
        self.assertEqual(pending["state"], "completion-pending")
        code, completed, stderr = self.run_cli(self.phase_request(topic, "finalize-phase-run", 7, phase_run_id=prepared["phase_run_id"], attempt_id=prepared["attempt_id"], evidence=evidence))
        self.assertEqual(code, 0, stderr)
        self.assertEqual(completed["ledger_revision"], 8)
        self.assertEqual(completed["current_phase"], 1)
        ledger_text = Path(str(topic["ledger_path"])).read_text(encoding="utf-8")
        self.assertIn("ledger_revision: 8", ledger_text)
        self.assertIn("event_count: 8", ledger_text)
        self.assertEqual(ledger_text.count("event_id: "), 8)
        self.assertEqual(ledger_text.count("ledger_revision: 8"), 2)

    def test_phase_drift_and_late_terminal_are_rejected(self) -> None:
        project = self.make_project("phase-drift", git=False)
        topic = self.bootstrap_topic(project)
        prepared = self.prepare_phase_run(topic)
        evidence = prepared["evidence"]
        topic_path = Path(str(topic["topic_document_path"]))
        _, _, _ = self.run_cli(self.phase_request(topic, "authorize-phase-carrier", 2, phase_run_id=prepared["phase_run_id"], attempt_id=prepared["attempt_id"], carrier_ref="discussion-task"))
        _, ready, _ = self.run_cli(self.phase_request(topic, "phase-ready", 3, phase_run_id=prepared["phase_run_id"], attempt_id=prepared["attempt_id"], carrier_ref="discussion-task", evidence=evidence))
        topic_path.write_text(topic_path.read_text(encoding="utf-8") + "\nchanged\n", encoding="utf-8")
        code, drifted, _ = self.run_cli(self.phase_request(topic, "phase-activate", 4, phase_run_id=prepared["phase_run_id"], attempt_id=prepared["attempt_id"], evidence=evidence))
        self.assertEqual(code, 1)
        self.assertEqual(drifted["error"]["code"], "phase_source_drift")
        topic_path.write_text(topic_path.read_text(encoding="utf-8").removesuffix("\nchanged\n"), encoding="utf-8")
        _, cancelled, _ = self.run_cli(self.phase_request(topic, "cancel-phase-run", 4, phase_run_id=prepared["phase_run_id"], attempt_id=prepared["attempt_id"], reason="cancel"))
        code, late, _ = self.run_cli(self.phase_request(topic, "claim-phase-completion", 5, phase_run_id=prepared["phase_run_id"], attempt_id=prepared["attempt_id"], carrier_ref="discussion-task", evidence=evidence))
        self.assertEqual(code, 1)
        self.assertEqual(late["error"]["code"], "phase_attempt_state_conflict")

    def test_discovery_none_ambiguous_and_document_only_are_zero_write(self) -> None:
        project = self.make_project("discovery", git=False)
        before = sorted(project.rglob("*"))
        code, none, stderr = self.run_cli({"protocol_version": 1, "operation": "discover-context", "project_path": str(project)})
        self.assertEqual(code, 0, stderr)
        self.assertEqual(none["context"], "none")
        self.assertEqual(before, sorted(project.rglob("*")))
        root = project / "docs" / "discussions"
        for number in (1, 2):
            path = root / f"topic-{number}" / "topic.md"
            path.parent.mkdir(parents=True)
            path.write_text("---\nproject_id: project-" + "1" * 32 + "\ntree_id: tree-" + "2" * 32 + "\ntopic_id: topic-" + f"{number}" * 32 + "\n---\n", encoding="utf-8")
        snapshot = {path: path.read_bytes() for path in project.rglob("*") if path.is_file()}
        _, ambiguous, _ = self.run_cli({"protocol_version": 1, "operation": "discover-context", "project_path": str(project)})
        self.assertEqual(ambiguous["context"], "ambiguous")
        self.assertEqual(snapshot, {path: path.read_bytes() for path in project.rglob("*") if path.is_file()})

    def test_discovery_prioritizes_authenticated_child_identity_and_active_binding(self) -> None:
        project = self.make_project("discovery-child", git=False)
        topic = self.bootstrap_topic(project)
        prepared = self.prepare_child_handoff(topic)
        child_identity = {
            "project_id": topic["project_id"],
            "tree_id": topic["tree_id"],
            "topic_id": prepared["target_topic_id"],
        }

        code, authenticated, stderr = self.run_cli(
            {
                "protocol_version": 1,
                "operation": "discover-context",
                "project_path": str(project),
                "authenticated_identity": child_identity,
            }
        )
        self.assertEqual(code, 0, stderr)
        self.assertEqual(authenticated["topic_id"], prepared["target_topic_id"])

        code, bound, stderr = self.run_cli(
            self.handoff_request(
                topic,
                operation="bind-handoff",
                ledger_revision=2,
                handoff_id=prepared["handoff_id"],
                attempt_id=prepared["attempt_id"],
                conversation_ref="codex-thread:child-discovery",
                verified_identity={
                    **child_identity,
                    "handoff_id": prepared["handoff_id"],
                    "attempt_id": prepared["attempt_id"],
                    "payload_sha256": prepared["payload_sha256"],
                },
            )
        )
        self.assertEqual(code, 0, stderr)
        self.assertEqual(bound["active_conversation_ref"], "codex-thread:child-discovery")

        code, active, stderr = self.run_cli(
            {
                "protocol_version": 1,
                "operation": "discover-context",
                "project_path": str(project),
                "conversation_ref": "codex-thread:child-discovery",
            }
        )
        self.assertEqual(code, 0, stderr)
        self.assertEqual(active["topic_id"], prepared["target_topic_id"])

        code, conflict, _ = self.run_cli(
            {
                "protocol_version": 1,
                "operation": "discover-context",
                "project_path": str(project),
                "authenticated_identity": {
                    "project_id": topic["project_id"],
                    "tree_id": topic["tree_id"],
                    "topic_id": topic["topic_id"],
                },
                "conversation_ref": "codex-thread:child-discovery",
            }
        )
        self.assertEqual(code, 1)
        self.assertEqual(conflict["error"]["code"], "discussion_identity_conflict")

    def test_discovery_without_ledger_selects_exact_child_evidence_zero_write(self) -> None:
        project = self.make_project("discovery-child-document-only", git=False)
        topic = self.bootstrap_topic(project)
        child_topic_id = "topic-" + "c" * 32
        child_path = project / "docs" / "discussions" / "api-shape" / "topic.md"
        child_path.parent.mkdir(parents=True)
        child_path.write_text(
            "---\n"
            "schema_version: 1\n"
            f"project_id: {topic['project_id']}\n"
            f"tree_id: {topic['tree_id']}\n"
            f"topic_id: {child_topic_id}\n"
            f"parent_topic_id: {topic['topic_id']}\n"
            "topic_revision: 1\n"
            "---\n"
            "# API shape\n",
            encoding="utf-8",
        )
        Path(str(topic["ledger_path"])).unlink()
        snapshot = {
            path: path.read_bytes() for path in project.rglob("*") if path.is_file()
        }
        child_identity = {
            "project_id": topic["project_id"],
            "tree_id": topic["tree_id"],
            "topic_id": child_topic_id,
        }

        evidence_requests = (
            {"authenticated_identity": child_identity},
            {"document_path": str(child_path)},
            {
                "authenticated_identity": child_identity,
                "document_path": str(child_path),
            },
        )
        for evidence in evidence_requests:
            with self.subTest(evidence=sorted(evidence)):
                code, discovered, stderr = self.run_cli(
                    {
                        "protocol_version": 1,
                        "operation": "discover-context",
                        "project_path": str(project),
                        **evidence,
                    }
                )
                self.assertEqual(code, 0, stderr)
                self.assertEqual(discovered["context"], "document_only")
                self.assertEqual(discovered["topic_id"], child_topic_id)
                self.assertEqual(discovered["topic_document_path"], str(child_path))

        code, conflict, _ = self.run_cli(
            {
                "protocol_version": 1,
                "operation": "discover-context",
                "project_path": str(project),
                "authenticated_identity": {
                    "project_id": topic["project_id"],
                    "tree_id": topic["tree_id"],
                    "topic_id": topic["topic_id"],
                },
                "document_path": str(child_path),
            }
        )
        self.assertEqual(code, 1)
        self.assertEqual(conflict["error"]["code"], "discussion_identity_conflict")

        code, fallback, stderr = self.run_cli(
            {
                "protocol_version": 1,
                "operation": "discover-context",
                "project_path": str(project),
            }
        )
        self.assertEqual(code, 0, stderr)
        self.assertEqual(fallback["context"], "document_only")
        self.assertEqual(fallback["topic_id"], topic["topic_id"])
        self.assertEqual(fallback["topic_document_path"], topic["topic_document_path"])
        self.assertEqual(
            snapshot,
            {path: path.read_bytes() for path in project.rglob("*") if path.is_file()},
        )

    def test_every_legal_route_and_illegal_route_matrix(self) -> None:
        legal = {(0, 1), (0, 2), (1, 2), (1, 3), (2, 3), (3, 4)}
        evidence = {key: "0" * 64 for key in ("source", "route", "impact", "coverage", "dependency", "coordination")}
        for source in range(5):
            for target in range(5):
                project = self.make_project(f"route-{source}-{target}", git=False)
                topic = self.bootstrap_topic(project)
                if source:
                    ledger = Path(str(topic["ledger_path"]))
                    self.rewrite_ledger_with_valid_digest(ledger, "current_phase: 0", f"current_phase: {source}")
                request = self.phase_request(topic, "prepare-phase-run", 1, from_phase=source, to_phase=target, route=f"{source}->{target}", carrier_kind="worker")
                code, response, _ = self.run_cli(request)
                if (source, target) in legal:
                    self.assertEqual(code, 0, (source, target, response))
                else:
                    self.assertEqual(code, 1, (source, target, response))
                    self.assertEqual(response["error"]["code"], "invalid_phase_route")

    def test_failed_attempt_retries_monotonically_and_duplicate_terminal_is_rejected(self) -> None:
        project = self.make_project("phase-retry", git=False)
        topic = self.bootstrap_topic(project)
        prepared = self.prepare_phase_run(topic)
        evidence = prepared["evidence"]
        _, _, _ = self.run_cli(self.phase_request(topic, "authorize-phase-carrier", 2, phase_run_id=prepared["phase_run_id"], attempt_id=prepared["attempt_id"], carrier_ref="discussion-task"))
        _, ready, _ = self.run_cli(self.phase_request(topic, "phase-ready", 3, phase_run_id=prepared["phase_run_id"], attempt_id=prepared["attempt_id"], carrier_ref="discussion-task", evidence=evidence))
        _, failed, _ = self.run_cli(self.phase_request(topic, "fail-phase-run", 4, phase_run_id=prepared["phase_run_id"], attempt_id=prepared["attempt_id"], reason="provider failed"))
        _, retried, _ = self.run_cli(self.phase_request(topic, "retry-phase-run", 5, phase_run_id=prepared["phase_run_id"], prior_attempt_id=prepared["attempt_id"]))
        self.assertEqual(retried["state"], "prepared")
        self.assertTrue(str(retried["attempt_id"]).endswith("-2"))
        _, _, _ = self.run_cli(self.phase_request(topic, "authorize-phase-carrier", 6, phase_run_id=prepared["phase_run_id"], attempt_id=retried["attempt_id"], carrier_ref="discussion-task"))
        code, second_ready, stderr = self.run_cli(self.phase_request(topic, "phase-ready", 7, phase_run_id=prepared["phase_run_id"], attempt_id=retried["attempt_id"], carrier_ref="discussion-task", evidence=evidence))
        self.assertEqual(code, 0, stderr)
        _, cancelled, _ = self.run_cli(self.phase_request(topic, "cancel-phase-run", 8, phase_run_id=prepared["phase_run_id"], attempt_id=retried["attempt_id"], reason="cancelled"))
        code, duplicate, _ = self.run_cli(self.phase_request(topic, "cancel-phase-run", 9, phase_run_id=prepared["phase_run_id"], attempt_id=retried["attempt_id"], reason="again"))
        self.assertEqual(code, 1)
        self.assertEqual(duplicate["error"]["code"], "phase_attempt_state_conflict")

    def test_phase_run_and_attempt_identities_are_monotonic(self) -> None:
        project = self.make_project("phase-identities", git=False)
        topic = self.bootstrap_topic(project)
        first = self.prepare_phase_run(topic)
        self.assertEqual(first["phase_run_id"], "PR-00000002")
        self.assertEqual(first["attempt_id"], "PA-00000002-1")
        _, _, _ = self.run_cli(
            self.phase_request(
                topic,
                "cancel-phase-run",
                2,
                phase_run_id=first["phase_run_id"],
                attempt_id=first["attempt_id"],
                reason="use a replacement run",
            )
        )
        second = self.prepare_phase_run(topic, revision=3)
        self.assertEqual(second["phase_run_id"], "PR-00000004")
        self.assertEqual(second["attempt_id"], "PA-00000004-1")

    def test_phase_run_mutations_reject_another_owned_topic(self) -> None:
        project = self.make_project("phase-source-identity", git=False)
        source = self.bootstrap_topic(project)
        handoff = self.prepare_child_handoff(source)
        bind = self.handoff_request(
            source,
            operation="bind-handoff",
            ledger_revision=2,
            handoff_id=handoff["handoff_id"],
            attempt_id=handoff["attempt_id"],
            conversation_ref="discussion-task",
            verified_identity={
                "project_id": source["project_id"],
                "tree_id": source["tree_id"],
                "topic_id": handoff["target_topic_id"],
                "handoff_id": handoff["handoff_id"],
                "attempt_id": handoff["attempt_id"],
                "payload_sha256": handoff["payload_sha256"],
            },
        )
        code, _, stderr = self.run_cli(bind)
        self.assertEqual(code, 0, stderr)
        prepared = self.prepare_phase_run(source, revision=3)
        foreign_topic = {**source, "topic_id": handoff["target_topic_id"]}
        common = {
            "phase_run_id": prepared["phase_run_id"],
            "attempt_id": prepared["attempt_id"],
        }
        mutations = {
            "retry-phase-run": {
                "phase_run_id": prepared["phase_run_id"],
                "prior_attempt_id": prepared["attempt_id"],
            },
            "reconcile-phase-run": {
                **common,
                "outcome": "not-created",
                "reason": "foreign reconciliation",
                "evidence": prepared["evidence"],
            },
            "revoke-phase-authorization": {**common, "reason": "foreign revocation"},
            "authorize-phase-carrier": {**common, "carrier_ref": "discussion-task"},
            "complete-phase-run": {**common, "evidence": prepared["evidence"]},
            "finalize-phase-run": {**common, "evidence": prepared["evidence"]},
            "supersede-phase-run": {**common, "reason": "foreign supersession"},
        }
        for operation, parameters in mutations.items():
            with self.subTest(operation=operation):
                code, rejected, _ = self.run_cli(
                    self.phase_request(foreign_topic, operation, 4, **parameters)
                )
                self.assertEqual(code, 1)
                self.assertEqual(rejected["error"]["code"], "phase_identity_conflict")

        read_request = self.phase_request(
            source,
            "read-phase-run",
            0,
            phase_run_id=prepared["phase_run_id"],
        )
        for key in ("expected_ledger_revision", "expected_topic_revision", "idempotency_key"):
            read_request.pop(key)
        code, read, stderr = self.run_cli(read_request)
        self.assertEqual(code, 0, stderr)
        self.assertEqual(read["ledger_revision"], 4)
        self.assertEqual(read["phase_run"]["state"], "prepared")

    def test_phase_evidence_is_required_and_stale_footer_dimensions_are_rejected(self) -> None:
        for dimension in ("source", "route", "impact", "coverage", "dependency", "coordination"):
            project = self.make_project(f"phase-evidence-{dimension}", git=False)
            topic = self.bootstrap_topic(project)
            prepared = self.prepare_phase_run(topic)
            _, _, _ = self.run_cli(
                self.phase_request(
                    topic,
                    "authorize-phase-carrier",
                    2,
                    phase_run_id=prepared["phase_run_id"],
                    attempt_id=prepared["attempt_id"],
                    carrier_ref="discussion-task",
                )
            )
            stale = dict(prepared["evidence"])
            stale[dimension] = "f" * 64
            ready = self.phase_request(
                topic,
                "phase-ready",
                3,
                phase_run_id=prepared["phase_run_id"],
                attempt_id=prepared["attempt_id"],
                carrier_ref="discussion-task",
                evidence=stale,
            )
            code, rejected, _ = self.run_cli(ready)
            self.assertEqual(code, 1, dimension)
            self.assertEqual(rejected["error"]["code"], f"phase_{dimension}_drift")

        project = self.make_project("phase-invalid-evidence", git=False)
        topic = self.bootstrap_topic(project)
        prepared = self.prepare_phase_run(topic)
        _, _, _ = self.run_cli(
            self.phase_request(
                topic,
                "authorize-phase-carrier",
                2,
                phase_run_id=prepared["phase_run_id"],
                attempt_id=prepared["attempt_id"],
                carrier_ref="discussion-task",
            )
        )
        invalid = self.phase_request(
            topic,
            "phase-ready",
            3,
            phase_run_id=prepared["phase_run_id"],
            attempt_id=prepared["attempt_id"],
            carrier_ref="discussion-task",
            evidence={"source": "not-a-digest"},
        )
        code, rejected, _ = self.run_cli(invalid)
        self.assertEqual(code, 1)
        self.assertEqual(rejected["error"]["code"], "invalid_request")

    def test_each_authoritative_drift_dimension_rejects_activation(self) -> None:
        replacements = {
            "source": ("topic-document", ""),
            "route": ("phase_state: \"active\"", "phase_state: \"paused\""),
            "impact": ("## Impacts\n\n```yaml\nrecords:\n  []\n```", "## Impacts\n\n```yaml\nrecords:\n  - impact_id: \"IMP-drift\"\n    topic_id: \"TOPIC_ID\"\n    data_json: \"{}\"\n```"),
            "coverage": ("## Relations and Coverage\n\n```yaml\nrecords:\n  []\n```", "## Relations and Coverage\n\n```yaml\nrecords:\n  - relation_id: \"REL-drift\"\n    source_topic_id: \"TOPIC_ID\"\n    target_topic_id: \"topic-99999999999999999999999999999999\"\n```"),
            "dependency": ("## Dependencies and Active Implementations\n\n```yaml\nrecords:\n  []\n```", "## Dependencies and Active Implementations\n\n```yaml\nrecords:\n  - dependency_id: \"DEP-drift\"\n    state: \"unknown\"\n```"),
            "coordination": (
                "binding_state: \"active\"\n    record_revision: 1",
                "binding_state: \"active\"\n    record_revision: 2",
            ),
        }
        for dimension, (old, new) in replacements.items():
            project = self.make_project(f"authority-drift-{dimension}", git=False)
            topic = self.bootstrap_topic(project)
            prepared = self.prepare_phase_run(topic)
            evidence = prepared["evidence"]
            _, _, _ = self.run_cli(
                self.phase_request(
                    topic,
                    "authorize-phase-carrier",
                    2,
                    phase_run_id=prepared["phase_run_id"],
                    attempt_id=prepared["attempt_id"],
                    carrier_ref="discussion-task",
                )
            )
            _, _, _ = self.run_cli(
                self.phase_request(
                    topic,
                    "phase-ready",
                    3,
                    phase_run_id=prepared["phase_run_id"],
                    attempt_id=prepared["attempt_id"],
                    carrier_ref="discussion-task",
                    evidence=evidence,
                )
            )
            if dimension == "source":
                topic_path = Path(str(topic["topic_document_path"]))
                topic_path.write_text(topic_path.read_text(encoding="utf-8") + "\ndrift\n", encoding="utf-8")
            else:
                ledger = Path(str(topic["ledger_path"]))
                self.rewrite_ledger_with_valid_digest(
                    ledger,
                    old,
                    new.replace("TOPIC_ID", str(topic["topic_id"])),
                )
            code, rejected, _ = self.run_cli(
                self.phase_request(
                    topic,
                    "phase-activate",
                    4,
                    phase_run_id=prepared["phase_run_id"],
                    attempt_id=prepared["attempt_id"],
                    evidence=evidence,
                )
            )
            self.assertEqual(code, 1, dimension)
            self.assertEqual(rejected["error"]["code"], f"phase_{dimension}_drift")

    def test_supersession_and_authorization_loss_reject_late_carrier_signals(self) -> None:
        for operation in ("supersede-phase-run", "revoke-phase-authorization"):
            project = self.make_project(f"late-{operation}", git=False)
            topic = self.bootstrap_topic(project)
            prepared = self.prepare_phase_run(topic)
            evidence = prepared["evidence"]
            _, _, _ = self.run_cli(
                self.phase_request(
                    topic,
                    "authorize-phase-carrier",
                    2,
                    phase_run_id=prepared["phase_run_id"],
                    attempt_id=prepared["attempt_id"],
                    carrier_ref="discussion-task",
                )
            )
            _, _, _ = self.run_cli(
                self.phase_request(
                    topic,
                    "phase-ready",
                    3,
                    phase_run_id=prepared["phase_run_id"],
                    attempt_id=prepared["attempt_id"],
                    carrier_ref="discussion-task",
                    evidence=evidence,
                )
            )
            _, _, _ = self.run_cli(
                self.phase_request(
                    topic,
                    "phase-activate",
                    4,
                    phase_run_id=prepared["phase_run_id"],
                    attempt_id=prepared["attempt_id"],
                    evidence=evidence,
                )
            )
            _, terminal, stderr = self.run_cli(
                self.phase_request(
                    topic,
                    operation,
                    5,
                    phase_run_id=prepared["phase_run_id"],
                    attempt_id=prepared["attempt_id"],
                    reason="source stopped this carrier",
                )
            )
            self.assertEqual(stderr, "")
            self.assertEqual(terminal["state"], "superseded" if operation.startswith("supersede") else "blocked")
            claim = self.phase_request(
                topic,
                "claim-phase-completion",
                6,
                phase_run_id=prepared["phase_run_id"],
                attempt_id=prepared["attempt_id"],
                carrier_ref="discussion-task",
                evidence=evidence,
            )
            code, late, _ = self.run_cli(claim)
            self.assertEqual(code, 1)
            self.assertEqual(late["error"]["code"], "phase_attempt_state_conflict")

    def test_outcome_unknown_requires_reconciliation_and_preserves_monotonic_attempts(self) -> None:
        for outcome, expected_state in (
            ("not-created", "failed"),
            ("not-completed", "failed"),
            ("completed", "completion-claimed"),
        ):
            project = self.make_project(f"reconcile-{outcome}", git=False)
            topic = self.bootstrap_topic(project)
            prepared = self.prepare_phase_run(topic)
            evidence = prepared["evidence"]
            _, _, _ = self.run_cli(
                self.phase_request(
                    topic,
                    "authorize-phase-carrier",
                    2,
                    phase_run_id=prepared["phase_run_id"],
                    attempt_id=prepared["attempt_id"],
                    carrier_ref="discussion-task",
                )
            )
            _, _, _ = self.run_cli(
                self.phase_request(
                    topic,
                    "phase-ready",
                    3,
                    phase_run_id=prepared["phase_run_id"],
                    attempt_id=prepared["attempt_id"],
                    carrier_ref="discussion-task",
                    evidence=evidence,
                )
            )
            _, _, _ = self.run_cli(
                self.phase_request(
                    topic,
                    "phase-activate",
                    4,
                    phase_run_id=prepared["phase_run_id"],
                    attempt_id=prepared["attempt_id"],
                    evidence=evidence,
                )
            )
            _, unknown, _ = self.run_cli(
                self.phase_request(
                    topic,
                    "phase-outcome-unknown",
                    5,
                    phase_run_id=prepared["phase_run_id"],
                    attempt_id=prepared["attempt_id"],
                    reason="provider result unavailable",
                )
            )
            self.assertEqual(unknown["state"], "outcome-unknown")
            code, retry_rejected, _ = self.run_cli(
                self.phase_request(
                    topic,
                    "retry-phase-run",
                    6,
                    phase_run_id=prepared["phase_run_id"],
                    prior_attempt_id=prepared["attempt_id"],
                )
            )
            self.assertEqual(code, 1)
            self.assertEqual(retry_rejected["error"]["code"], "phase_reconciliation_required")
            _, reconciled, _ = self.run_cli(
                self.phase_request(
                    topic,
                    "reconcile-phase-run",
                    6,
                    phase_run_id=prepared["phase_run_id"],
                    attempt_id=prepared["attempt_id"],
                    outcome=outcome,
                    reason="verified provider outcome",
                    evidence=evidence,
                )
            )
            self.assertEqual(reconciled["state"], expected_state)
            if expected_state == "failed":
                _, retried, _ = self.run_cli(
                    self.phase_request(
                        topic,
                        "retry-phase-run",
                        7,
                        phase_run_id=prepared["phase_run_id"],
                        prior_attempt_id=prepared["attempt_id"],
                    )
                )
                self.assertTrue(str(retried["attempt_id"]).endswith("-2"))

    def test_reopen_requires_explicit_affected_decision_review(self) -> None:
        project = self.make_project("phase-reopen", git=False)
        topic = self.bootstrap_topic(project)
        code, decision, stderr = self.run_cli(
            self.evolution_request(
                topic,
                operation="prepare-topic-update",
                expected_revision=1,
                mutation={
                    "type": "confirm-decision",
                    "summary": "Keep lifecycle state durable.",
                    "rationale": "Reopen must review this result.",
                },
            )
        )
        self.assertEqual(code, 0, stderr)
        decision_id = decision["decision_id"]
        ledger = Path(str(topic["ledger_path"]))
        self.rewrite_ledger_with_valid_digest(ledger, "current_phase: 0", "current_phase: 2")
        missing = self.phase_request(
            topic,
            "reopen-phase",
            2,
            topic_revision=2,
            affected_decision_ids=[decision_id],
            review={},
            reason="requirements changed",
        )
        code, rejected, _ = self.run_cli(missing)
        self.assertEqual(code, 1)
        self.assertEqual(rejected["error"]["code"], "phase_reopen_review_required")
        reopened = dict(missing)
        reopened["idempotency_key"] = str(uuid.uuid4())
        reopened["review"] = {decision_id: "adjust"}
        code, result, stderr = self.run_cli(reopened)
        self.assertEqual(code, 0, stderr)
        self.assertEqual(result["state"], "reopened")
        self.assertEqual(result["current_phase"], 0)

    def test_reopen_rejects_omitted_authoritative_decisions_and_marks_results_for_review(self) -> None:
        project = self.make_project("phase-reopen-complete-review", git=False)
        topic = self.bootstrap_topic(project)
        decisions = []
        ledger_revision = 1
        topic_revision = 1
        for number in (1, 2):
            prepared, ledger_revision, topic_revision = self.complete_update(
                project,
                topic,
                ledger_revision=ledger_revision,
                topic_revision=topic_revision,
                mutation={
                    "type": "confirm-decision",
                    "summary": f"Stable decision {number}",
                    "rationale": "The completed phase depends on this decision.",
                },
            )
            decisions.append(str(prepared["decision_id"]))

        code, prepared, stderr = self.run_cli(
            self.phase_request(
                topic,
                "prepare-phase-run",
                ledger_revision,
                topic_revision=topic_revision,
                from_phase=0,
                to_phase=1,
                route="0->1",
                carrier_kind="problem-framing",
            )
        )
        self.assertEqual(code, 0, stderr)
        evidence = prepared["evidence"]
        transitions = (
            (
                "authorize-phase-carrier",
                {
                    "carrier_ref": "discussion-task",
                },
            ),
            (
                "phase-ready",
                {
                    "carrier_ref": "discussion-task",
                    "evidence": evidence,
                },
            ),
            ("phase-activate", {"evidence": evidence}),
            (
                "claim-phase-completion",
                {
                    "carrier_ref": "discussion-task",
                    "evidence": evidence,
                },
            ),
            ("complete-phase-run", {"evidence": evidence}),
            ("finalize-phase-run", {"evidence": evidence}),
        )
        completed = None
        for offset, (operation, parameters) in enumerate(transitions, start=1):
            code, response, stderr = self.run_cli(
                self.phase_request(
                    topic,
                    operation,
                    ledger_revision + offset,
                    topic_revision=topic_revision,
                    phase_run_id=prepared["phase_run_id"],
                    attempt_id=prepared["attempt_id"],
                    **parameters,
                )
            )
            self.assertEqual(code, 0, stderr)
            completed = response
        assert completed is not None

        omitted = self.phase_request(
            topic,
            "reopen-phase",
            ledger_revision + len(transitions) + 1,
            topic_revision=topic_revision + 1,
            affected_decision_ids=[decisions[0]],
            review={decisions[0]: "adjust"},
            reason="Both stable decisions changed.",
        )
        code, rejected, _ = self.run_cli(omitted)
        self.assertEqual(code, 1)
        self.assertEqual(rejected["error"]["code"], "phase_reopen_review_required")

        complete = dict(omitted)
        complete["idempotency_key"] = str(uuid.uuid4())
        complete["affected_decision_ids"] = decisions
        complete["review"] = {decision_id: "adjust" for decision_id in decisions}
        code, reopened, stderr = self.run_cli(complete)
        self.assertEqual(code, 0, stderr)
        self.assertEqual(reopened["affected_decision_ids"], sorted(decisions))
        self.assertEqual(
            reopened["review_pending_result_ids"],
            [completed["phase_result_id"]],
        )

        code, replayed, stderr = self.run_cli(complete)
        self.assertEqual(code, 0, stderr)
        self.assertTrue(replayed["idempotent_replay"])
        self.assertEqual(replayed["ledger_revision"], reopened["ledger_revision"])
        self.assertEqual(
            replayed["review_pending_result_ids"],
            reopened["review_pending_result_ids"],
        )

    def test_document_only_discovery_and_authorized_verified_import(self) -> None:
        project = self.make_project("document-only", git=False)
        seed = self.bootstrap_topic(project)
        ledger = Path(str(seed["ledger_path"]))
        ledger.unlink()
        result_path = project / "phase-result.md"
        result_id = "PH-" + "2" * 32
        result_path.write_text(
            "---\n"
            f"project_id: {seed['project_id']}\n"
            f"tree_id: {seed['tree_id']}\n"
            f"topic_id: {seed['topic_id']}\n"
            f"result_id: {result_id}\n"
            "phase: 1\n"
            "state: completed\n"
            "---\n"
            "verified result\n",
            encoding="utf-8",
        )
        digest = hashlib.sha256(result_path.read_bytes()).hexdigest()
        before = {path: path.read_bytes() for path in project.rglob("*") if path.is_file()}
        code, discovered, stderr = self.run_cli(
            {
                "protocol_version": 1,
                "operation": "discover-context",
                "project_path": str(project),
                "document_path": seed["topic_document_path"],
            }
        )
        self.assertEqual(code, 0, stderr)
        self.assertEqual(discovered["context"], "document_only")
        self.assertEqual(discovered["coordination_state"], "unknown")
        self.assertEqual(before, {path: path.read_bytes() for path in project.rglob("*") if path.is_file()})
        request = {
            "protocol_version": 1,
            "operation": "initialize-document-context",
            "project_path": str(project),
            "conversation_ref": "discussion-task",
            "idempotency_key": str(uuid.uuid4()),
            "user_authorization": False,
            "verified_results": [{
                "result_id": result_id,
                "phase": 1,
                "state": "completed",
                "path": str(result_path),
                "sha256": digest,
            }],
        }
        code, unauthorized, _ = self.run_cli(request)
        self.assertEqual(code, 1)
        self.assertEqual(unauthorized["error"]["code"], "context_not_initialized")
        request["user_authorization"] = True
        code, initialized, stderr = self.run_cli(request)
        self.assertEqual(code, 0, stderr)
        self.assertEqual(initialized["imported_result_count"], 1)
        self.assertEqual(initialized["coordination_state"], "unknown")

    def test_discovered_child_document_can_initialize_exact_authorized_context(self) -> None:
        project = self.make_project("document-only-child-initialize", git=False)
        root = self.bootstrap_topic(project)
        ledger = Path(str(root["ledger_path"]))
        ledger.unlink()
        child_topic_id = "topic-" + "c" * 32
        child_path = project / "docs" / "discussions" / "api-shape" / "topic.md"
        child_path.parent.mkdir(parents=True)
        child_path.write_text(
            "---\n"
            "schema_version: 1\n"
            f"project_id: {root['project_id']}\n"
            f"tree_id: {root['tree_id']}\n"
            f"topic_id: {child_topic_id}\n"
            f"parent_topic_id: {root['topic_id']}\n"
            "topic_revision: 1\n"
            "---\n"
            "# API shape\n",
            encoding="utf-8",
        )
        child_identity = {
            "project_id": root["project_id"],
            "tree_id": root["tree_id"],
            "topic_id": child_topic_id,
        }
        code, discovered, stderr = self.run_cli(
            {
                "protocol_version": 1,
                "operation": "discover-context",
                "project_path": str(project),
                "authenticated_identity": child_identity,
                "document_path": str(child_path),
            }
        )
        self.assertEqual(code, 0, stderr)
        self.assertEqual(discovered["context"], "document_only")
        self.assertEqual(discovered["topic_id"], child_topic_id)

        result_path = project / "child-phase-result.md"
        result_id = "PH-" + "5" * 32

        def write_result(topic_id: str) -> str:
            result_path.write_text(
                "---\n"
                f"project_id: {root['project_id']}\n"
                f"tree_id: {root['tree_id']}\n"
                f"topic_id: {topic_id}\n"
                f"result_id: {result_id}\n"
                "phase: 1\n"
                "state: completed\n"
                "---\n"
                "# Stable child result\n",
                encoding="utf-8",
            )
            return hashlib.sha256(result_path.read_bytes()).hexdigest()

        request = {
            "protocol_version": 1,
            "operation": "initialize-document-context",
            "project_path": str(project),
            "conversation_ref": "codex-thread:child-initialize",
            "idempotency_key": str(uuid.uuid4()),
            "user_authorization": False,
            "topic_identity": child_identity,
            "topic_document_path": discovered["topic_document_path"],
            "verified_results": [
                {
                    "result_id": result_id,
                    "phase": 1,
                    "state": "completed",
                    "path": str(result_path),
                    "sha256": write_result(child_topic_id),
                }
            ],
        }
        before = {
            path: path.read_bytes() for path in project.rglob("*") if path.is_file()
        }
        code, unauthorized, _ = self.run_cli(request)
        self.assertEqual(code, 1)
        self.assertEqual(unauthorized["error"]["code"], "context_not_initialized")
        self.assertEqual(
            before,
            {path: path.read_bytes() for path in project.rglob("*") if path.is_file()},
        )

        request["user_authorization"] = True
        request["topic_document_path"] = root["topic_document_path"]
        before_conflict = {
            path: path.read_bytes() for path in project.rglob("*") if path.is_file()
        }
        code, conflict, _ = self.run_cli(request)
        self.assertEqual(code, 1)
        self.assertEqual(conflict["error"]["code"], "context_identity_conflict")
        self.assertFalse(ledger.exists())
        self.assertEqual(
            before_conflict,
            {path: path.read_bytes() for path in project.rglob("*") if path.is_file()},
        )

        request["topic_document_path"] = discovered["topic_document_path"]
        request["verified_results"][0]["sha256"] = write_result(str(root["topic_id"]))
        before_result_conflict = {
            path: path.read_bytes() for path in project.rglob("*") if path.is_file()
        }
        code, result_conflict, _ = self.run_cli(request)
        self.assertEqual(code, 1)
        self.assertEqual(result_conflict["error"]["code"], "context_identity_conflict")
        self.assertFalse(ledger.exists())
        self.assertEqual(
            before_result_conflict,
            {path: path.read_bytes() for path in project.rglob("*") if path.is_file()},
        )

        request["verified_results"][0]["sha256"] = write_result(child_topic_id)
        code, initialized, stderr = self.run_cli(request)
        self.assertEqual(code, 0, stderr)
        self.assertTrue(initialized["created"])
        self.assertEqual(initialized["topic_id"], child_topic_id)
        self.assertEqual(initialized["topic_document_path"], str(child_path))
        self.assertEqual(initialized["imported_result_count"], 1)
        ledger_text = ledger.read_text(encoding="utf-8")
        current_topics = ledger_text.split("## Current Topics\n\n", 1)[1].split(
            "\n## Pending Items\n\n", 1
        )[0]
        bindings = ledger_text.split("## Conversation Bindings\n\n", 1)[1].split(
            "\n## Recent Events\n\n", 1
        )[0]
        phase_results = ledger_text.split("## Phase Results\n\n", 1)[1].split(
            "\n## Checkpoints\n\n", 1
        )[0]
        self.assertIn(f'topic_id: "{child_topic_id}"', current_topics)
        self.assertIn(f'topic_document_path: "{child_path}"', current_topics)
        self.assertNotIn(str(root["topic_id"]), current_topics)
        self.assertIn(f'topic_id: "{child_topic_id}"', bindings)
        self.assertIn('conversation_ref: "codex-thread:child-initialize"', bindings)
        self.assertIn(f'result_id: "{result_id}"', phase_results)
        self.assertIn(f'\\"topic_id\\":\\"{child_topic_id}\\"', phase_results)

        initialized_ledger = ledger.read_bytes()
        code, replayed, stderr = self.run_cli(request)
        self.assertEqual(code, 0, stderr)
        self.assertTrue(replayed["idempotent_replay"])
        self.assertEqual(replayed["topic_id"], child_topic_id)
        self.assertEqual(replayed["ledger_revision"], initialized["ledger_revision"])
        self.assertEqual(ledger.read_bytes(), initialized_ledger)

    def test_document_only_import_validates_metadata_and_initializes_atomically(self) -> None:
        project = self.make_project("document-only-atomic", git=False)
        seed = self.bootstrap_topic(project)
        ledger = Path(str(seed["ledger_path"]))
        ledger.unlink()
        result_path = project / "stable-phase-result.md"
        result_id = "PH-" + "3" * 32

        def write_result(*, file_result_id: str) -> str:
            result_path.write_text(
                "---\n"
                f"project_id: {seed['project_id']}\n"
                f"tree_id: {seed['tree_id']}\n"
                f"topic_id: {seed['topic_id']}\n"
                f"result_id: {file_result_id}\n"
                "phase: 1\n"
                "state: completed\n"
                "---\n"
                "# Stable phase result\n",
                encoding="utf-8",
            )
            return hashlib.sha256(result_path.read_bytes()).hexdigest()

        request = {
            "protocol_version": 1,
            "operation": "initialize-document-context",
            "project_path": str(project),
            "conversation_ref": "discussion-task",
            "idempotency_key": str(uuid.uuid4()),
            "user_authorization": True,
            "verified_results": [
                {
                    "result_id": result_id,
                    "phase": 1,
                    "state": "completed",
                    "path": str(result_path),
                    "sha256": write_result(file_result_id="PH-" + "4" * 32),
                }
            ],
        }
        code, mismatch, _ = self.run_cli(request)
        self.assertEqual(code, 1)
        self.assertEqual(mismatch["error"]["code"], "context_identity_conflict")
        self.assertFalse(ledger.exists())

        request["verified_results"][0]["sha256"] = write_result(file_result_id=result_id)
        code, injected, _ = self.run_cli(
            request,
            failpoint="document-context-before-ledger-create",
        )
        self.assertEqual(code, 1)
        self.assertEqual(injected["error"]["code"], "injected_failure")
        self.assertFalse(ledger.exists())

        code, initialized, stderr = self.run_cli(request)
        self.assertEqual(code, 0, stderr)
        self.assertTrue(initialized["created"])
        self.assertEqual(initialized["imported_result_count"], 1)

        code, replayed, stderr = self.run_cli(request)
        self.assertEqual(code, 0, stderr)
        self.assertTrue(replayed["idempotent_replay"])
        self.assertEqual(replayed["imported_result_count"], 1)
        self.assertEqual(replayed["ledger_revision"], initialized["ledger_revision"])

    def evolution_request(
        self,
        topic: dict[str, object],
        *,
        operation: str,
        expected_revision: int | None = None,
        expected_topic_revision: int | None = None,
        owner_ref: str = "discussion-task",
        **parameters: object,
    ) -> dict[str, object]:
        request: dict[str, object] = {
            "protocol_version": 1,
            "operation": operation,
            "project_path": str(Path(str(topic["topic_document_path"])).parents[3]),
            "project_id": topic["project_id"],
            "tree_id": topic["tree_id"],
            "actor_topic_id": topic["topic_id"],
            "actor_conversation_ref": owner_ref,
        }
        if expected_revision is not None:
            request.update(
                {
                    "expected_ledger_revision": expected_revision,
                    "expected_topic_revision": expected_topic_revision or expected_revision,
                    "idempotency_key": str(uuid.uuid4()),
                }
            )
        request.update(parameters)
        return request

    def supervision_cli(self, *arguments: str) -> dict[str, object]:
        completed = subprocess.run(
            [sys.executable, str(SUPERVISION_SCRIPT_PATH), *arguments],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        self.assertEqual(completed.returncode, 0, completed.stderr)
        return json.loads(completed.stdout)

    def acquire_document_lease(
        self, project: Path, *, owner_ref: str = "discussion-task"
    ) -> dict[str, object]:
        input_path = self.root / f"lease-{uuid.uuid4().hex}.json"
        input_path.write_text(
            json.dumps(
                {
                    "owner_host_id": "test-host",
                    "owner_task_id": owner_ref,
                    "purpose": "document-write",
                    "repository": str(project),
                    "stage": "design-discussion",
                    "ttl_seconds": 300,
                },
                sort_keys=True,
                separators=(",", ":"),
            )
            + "\n",
            encoding="utf-8",
        )
        return self.supervision_cli(
            "acquire-document-lease",
            "--wait-seconds",
            "0",
            "--max-retries",
            "0",
            "--input",
            str(input_path),
        )

    def release_document_lease(self, lease: dict[str, object]) -> dict[str, object]:
        holder = lease["holder"]
        assert isinstance(holder, dict)
        return self.supervision_cli(
            "release-document-lease",
            "--file",
            str(lease["path"]),
            "--id",
            str(holder["lease_id"]),
            "--version",
            str(lease["version"]),
        )

    def acquire_repository_coordination_lease(
        self, project: Path, *, owner_ref: str = "discussion-task"
    ) -> dict[str, object]:
        input_path = self.root / f"repository-coordination-{uuid.uuid4().hex}.json"
        input_path.write_text(
            json.dumps(
                {
                    "owner_host_id": "test-host",
                    "owner_task_id": owner_ref,
                    "purpose": "checkpoint-publish",
                    "repository": str(project),
                    "stage": "design-discussion",
                    "ttl_seconds": 300,
                },
                sort_keys=True,
                separators=(",", ":"),
            )
            + "\n",
            encoding="utf-8",
        )
        return self.supervision_cli(
            "acquire-repository-coordination-lease", "--input", str(input_path)
        )

    def checkpoint_request(
        self,
        topic: dict[str, object],
        *,
        operation: str,
        ledger_revision: int | None = None,
        topic_revision: int = 1,
        **parameters: object,
    ) -> dict[str, object]:
        return self.evolution_request(
            topic,
            operation=operation,
            expected_revision=ledger_revision,
            expected_topic_revision=topic_revision,
            **parameters,
        )

    def handoff_request(
        self,
        topic: dict[str, object],
        *,
        operation: str,
        ledger_revision: int | None = None,
        topic_revision: int = 1,
        owner_ref: str = "discussion-task",
        **parameters: object,
    ) -> dict[str, object]:
        return self.evolution_request(
            topic,
            operation=operation,
            expected_revision=ledger_revision,
            expected_topic_revision=topic_revision,
            owner_ref=owner_ref,
            **parameters,
        )

    def prepare_child_handoff(
        self,
        topic: dict[str, object],
        *,
        ledger_revision: int = 1,
        scope: list[str] | None = None,
        work_snapshot: dict[str, object] | None = None,
    ) -> dict[str, object]:
        returncode, prepared, stderr = self.run_cli(
            self.handoff_request(
                topic,
                operation="prepare-handoff",
                ledger_revision=ledger_revision,
                handoff_kind="child",
                target_slug="api-shape",
                scope=scope or ["api"],
                work_snapshot=work_snapshot
                or {
                    "goal": "Choose the public API shape.",
                    "confirmed_decisions": [],
                    "pending_questions": ["Which requests are public?"],
                },
                authoritative_references=[
                    {
                        "kind": "checkpoint",
                        "identity": "CP-source",
                        "sha256": "1" * 64,
                    }
                ],
            )
        )
        self.assertEqual(returncode, 0, stderr)
        return prepared

    def test_child_handoff_persists_topic_attempt_and_bounded_identity_payload(self) -> None:
        project = self.make_project("child-handoff", git=False)
        topic = self.bootstrap_topic(project)
        prepared = self.prepare_child_handoff(topic)
        self.assertEqual(prepared["state"], "setup-pending")
        self.assertRegex(str(prepared["handoff_id"]), r"^H-[0-9a-f]{32}$")
        self.assertRegex(str(prepared["attempt_id"]), r"^A-[0-9a-f]{32}-1$")
        self.assertRegex(str(prepared["target_topic_id"]), r"^topic-[0-9a-f]{32}$")
        self.assertNotEqual(prepared["target_topic_id"], topic["topic_id"])
        self.assertEqual(
            set(prepared["identity_envelope"]),
            {"project_id", "tree_id", "topic_id", "parent_topic_id", "handoff_id", "attempt_id"},
        )
        self.assertRegex(str(prepared["payload_sha256"]), r"^[0-9a-f]{64}$")
        self.assertLessEqual(prepared["work_snapshot_bytes"], 16000)
        self.assertLessEqual(prepared["handoff_payload_bytes"], 32000)

        returncode, read, stderr = self.run_cli(
            self.handoff_request(
                topic,
                operation="read-handoff",
                handoff_id=prepared["handoff_id"],
            )
        )
        self.assertEqual(returncode, 0, stderr)
        self.assertEqual(read["handoff"]["state"], "setup-pending")
        self.assertEqual(read["attempts"][0]["state"], "setup-pending")
        self.assertEqual(read["target_topic"]["parent_topic_id"], topic["topic_id"])

        oversized = self.handoff_request(
            topic,
            operation="prepare-handoff",
            ledger_revision=2,
            handoff_kind="child",
            target_slug="too-large",
            scope=["api"],
            work_snapshot={"goal": "x" * 16001},
            authoritative_references=[],
        )
        returncode, rejected, _ = self.run_cli(oversized)
        self.assertEqual(returncode, 1)
        self.assertEqual(rejected["error"]["code"], "handoff_payload_too_large")

    def test_handoff_requires_verified_binding_then_first_turn_acceptance_and_later_turn(self) -> None:
        project = self.make_project("handoff-gate", git=False)
        topic = self.bootstrap_topic(project)
        prepared = self.prepare_child_handoff(topic)
        bind = self.handoff_request(
            topic,
            operation="bind-handoff",
            ledger_revision=2,
            handoff_id=prepared["handoff_id"],
            attempt_id=prepared["attempt_id"],
            conversation_ref="codex-thread:child-real",
            verified_identity={
                "project_id": topic["project_id"],
                "tree_id": topic["tree_id"],
                "topic_id": prepared["target_topic_id"],
                "handoff_id": prepared["handoff_id"],
                "attempt_id": prepared["attempt_id"],
                "payload_sha256": prepared["payload_sha256"],
            },
        )
        returncode, bound, stderr = self.run_cli(bind)
        self.assertEqual(returncode, 0, stderr)
        self.assertEqual(bound["state"], "bound-pending-acceptance")

        accept = self.handoff_request(
            topic,
            operation="accept-handoff",
            ledger_revision=3,
            owner_ref="codex-thread:child-real",
            handoff_id=prepared["handoff_id"],
            attempt_id=prepared["attempt_id"],
            payload_sha256=prepared["payload_sha256"],
            source_reference_sha256=prepared["authoritative_references_sha256"],
            turn_number=1,
        )
        accept["actor_topic_id"] = prepared["target_topic_id"]
        returncode, accepted, stderr = self.run_cli(accept)
        self.assertEqual(returncode, 0, stderr)
        self.assertEqual(accepted["state"], "accepted-awaiting-next-turn")
        self.assertFalse(accepted["substantive_discussion_allowed"])

        same_turn = self.handoff_request(
            topic,
            operation="authorize-handoff-discussion",
            ledger_revision=4,
            owner_ref="codex-thread:child-real",
            handoff_id=prepared["handoff_id"],
            attempt_id=prepared["attempt_id"],
            turn_number=1,
        )
        same_turn["actor_topic_id"] = prepared["target_topic_id"]
        returncode, rejected, _ = self.run_cli(same_turn)
        self.assertEqual(returncode, 1)
        self.assertEqual(rejected["error"]["code"], "handoff_next_turn_required")

        next_turn = dict(same_turn)
        next_turn["idempotency_key"] = str(uuid.uuid4())
        next_turn["turn_number"] = 2
        returncode, authorized, stderr = self.run_cli(next_turn)
        self.assertEqual(returncode, 0, stderr)
        self.assertTrue(authorized["substantive_discussion_allowed"])

    def test_outcome_unknown_cancel_late_arrival_and_forced_retry_preserve_attempt_history(self) -> None:
        project = self.make_project("handoff-recovery", git=False)
        topic = self.bootstrap_topic(project)
        prepared = self.prepare_child_handoff(topic)
        returncode, unknown, stderr = self.run_cli(
            self.handoff_request(
                topic,
                operation="record-handoff-outcome-unknown",
                ledger_revision=2,
                handoff_id=prepared["handoff_id"],
                attempt_id=prepared["attempt_id"],
            )
        )
        self.assertEqual(returncode, 0, stderr)
        self.assertEqual(unknown["state"], "outcome-unknown")

        returncode, blocked, _ = self.run_cli(
            self.handoff_request(
                topic,
                operation="retry-handoff",
                ledger_revision=3,
                handoff_id=prepared["handoff_id"],
                prior_attempt_id=prepared["attempt_id"],
                forced=False,
            )
        )
        self.assertEqual(returncode, 1)
        self.assertEqual(blocked["error"]["code"], "handoff_reconciliation_required")

        returncode, cancelled, stderr = self.run_cli(
            self.handoff_request(
                topic,
                operation="cancel-handoff-attempt",
                ledger_revision=3,
                handoff_id=prepared["handoff_id"],
                attempt_id=prepared["attempt_id"],
                reason="user abandoned uncertain creation",
            )
        )
        self.assertEqual(returncode, 0, stderr)
        self.assertEqual(cancelled["state"], "cancelled")
        self.assertFalse(cancelled["binding_eligible"])

        returncode, late, _ = self.run_cli(
            self.handoff_request(
                topic,
                operation="bind-handoff",
                ledger_revision=4,
                handoff_id=prepared["handoff_id"],
                attempt_id=prepared["attempt_id"],
                conversation_ref="codex-thread:late",
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
        self.assertEqual(returncode, 1)
        self.assertEqual(late["error"]["code"], "handoff_late_arrival")

        returncode, retried, stderr = self.run_cli(
            self.handoff_request(
                topic,
                operation="retry-handoff",
                ledger_revision=4,
                handoff_id=prepared["handoff_id"],
                prior_attempt_id=prepared["attempt_id"],
                forced=True,
                user_authorization="Create a replacement task despite the cancelled outcome.",
            )
        )
        self.assertEqual(returncode, 0, stderr)
        self.assertRegex(str(retried["attempt_id"]), r"^A-[0-9a-f]{32}-2$")
        self.assertNotEqual(retried["attempt_id"], prepared["attempt_id"])
        returncode, read, stderr = self.run_cli(
            self.handoff_request(topic, operation="read-handoff", handoff_id=prepared["handoff_id"])
        )
        self.assertEqual(returncode, 0, stderr)
        self.assertEqual([item["state"] for item in read["attempts"]], ["cancelled", "setup-pending"])

    def test_continuation_atomically_supersedes_binding_and_parallel_claim_has_one_winner(self) -> None:
        project = self.make_project("continuation-binding", git=False)
        topic = self.bootstrap_topic(project)
        returncode, prepared, stderr = self.run_cli(
            self.handoff_request(
                topic,
                operation="prepare-handoff",
                ledger_revision=1,
                handoff_kind="continuation",
                target_slug="checkout-redesign",
                scope=["root"],
                work_snapshot={"goal": "Continue an unavailable conversation."},
                authoritative_references=[],
            )
        )
        self.assertEqual(returncode, 0, stderr)
        self.assertEqual(prepared["target_topic_id"], topic["topic_id"])
        same_ref = self.handoff_request(
            topic,
            operation="bind-handoff",
            ledger_revision=2,
            handoff_id=prepared["handoff_id"],
            attempt_id=prepared["attempt_id"],
            conversation_ref="discussion-task",
            verified_identity={
                "project_id": topic["project_id"],
                "tree_id": topic["tree_id"],
                "topic_id": topic["topic_id"],
                "handoff_id": prepared["handoff_id"],
                "attempt_id": prepared["attempt_id"],
                "payload_sha256": prepared["payload_sha256"],
            },
        )
        returncode, rejected, _ = self.run_cli(same_ref)
        self.assertEqual(returncode, 1)
        self.assertEqual(rejected["error"]["code"], "handoff_identity_conflict")
        requests = []
        for suffix in ("one", "two"):
            requests.append(
                self.handoff_request(
                    topic,
                    operation="bind-handoff",
                    ledger_revision=2,
                    handoff_id=prepared["handoff_id"],
                    attempt_id=prepared["attempt_id"],
                    conversation_ref=f"codex-thread:continuation-{suffix}",
                    verified_identity={
                        "project_id": topic["project_id"],
                        "tree_id": topic["tree_id"],
                        "topic_id": topic["topic_id"],
                        "handoff_id": prepared["handoff_id"],
                        "attempt_id": prepared["attempt_id"],
                        "payload_sha256": prepared["payload_sha256"],
                    },
                )
            )
        with ThreadPoolExecutor(max_workers=2) as pool:
            outcomes = list(pool.map(self.run_cli, requests))
        successes = [item for item in outcomes if item[0] == 0]
        failures = [item for item in outcomes if item[0] == 1]
        self.assertEqual(len(successes), 1)
        self.assertEqual(len(failures), 1)
        self.assertIn(
            failures[0][1]["error"]["code"],
            {"ledger_revision_conflict", "handoff_attempt_state_conflict"},
        )
        winner = successes[0][1]
        self.assertEqual(winner["superseded_conversation_ref"], "discussion-task")
        self.assertEqual(winner["active_conversation_ref"], winner["conversation_ref"])

        returncode, read, stderr = self.run_cli(
            self.handoff_request(topic, operation="read-handoff", handoff_id=prepared["handoff_id"])
        )
        self.assertEqual(returncode, 0, stderr)
        active = [item for item in read["bindings"] if item["binding_state"] == "active"]
        self.assertEqual(len(active), 1)
        self.assertEqual(active[0]["conversation_ref"], winner["conversation_ref"])

        second = self.handoff_request(
            topic,
            operation="prepare-handoff",
            ledger_revision=3,
            owner_ref=winner["conversation_ref"],
            handoff_kind="continuation",
            target_slug="checkout-redesign",
            scope=["root"],
            work_snapshot={"goal": "Continue the continuation conversation."},
            authoritative_references=[],
        )
        returncode, second_prepared, stderr = self.run_cli(second)
        self.assertEqual(returncode, 0, stderr)
        returncode, second_bound, stderr = self.run_cli(
            self.handoff_request(
                topic,
                operation="bind-handoff",
                ledger_revision=4,
                owner_ref=winner["conversation_ref"],
                handoff_id=second_prepared["handoff_id"],
                attempt_id=second_prepared["attempt_id"],
                conversation_ref="codex-thread:continuation-three",
                verified_identity={
                    "project_id": topic["project_id"],
                    "tree_id": topic["tree_id"],
                    "topic_id": topic["topic_id"],
                    "handoff_id": second_prepared["handoff_id"],
                    "attempt_id": second_prepared["attempt_id"],
                    "payload_sha256": second_prepared["payload_sha256"],
                },
            )
        )
        self.assertEqual(returncode, 0, stderr)
        self.assertEqual(
            second_bound["superseded_conversation_ref"], winner["conversation_ref"]
        )
        validate = self.handoff_request(
            topic,
            operation="validate",
            owner_ref=second_bound["conversation_ref"],
        )
        returncode, valid, stderr = self.run_cli(validate)
        self.assertEqual(returncode, 0, stderr)
        self.assertEqual(valid["state"], "valid")
        self.assertEqual(valid["handoff_count"], 2)

    def test_child_result_absorbs_only_within_scope_and_records_cross_topic_impact(self) -> None:
        project = self.make_project("child-result", git=False)
        topic = self.bootstrap_topic(project)
        prepared = self.prepare_child_handoff(topic, scope=["api"])
        forged = self.handoff_request(
            topic,
            operation="record-child-result",
            ledger_revision=2,
            handoff_id=prepared["handoff_id"],
            child_result_id="CR-" + "0" * 32,
            effect="absorb",
        )
        returncode, rejected, _ = self.run_cli(forged)
        self.assertEqual(returncode, 1)
        self.assertEqual(rejected["error"]["code"], "record_not_found")

        child_ref = "codex-thread:result-child"
        returncode, bound, stderr = self.run_cli(
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
        self.assertEqual(returncode, 0, stderr)
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
        returncode, _, stderr = self.run_cli(accept)
        self.assertEqual(returncode, 0, stderr)
        authorize = self.handoff_request(
            topic,
            operation="authorize-handoff-discussion",
            ledger_revision=4,
            owner_ref=child_ref,
            handoff_id=prepared["handoff_id"],
            attempt_id=prepared["attempt_id"],
            turn_number=2,
        )
        authorize["actor_topic_id"] = prepared["target_topic_id"]
        returncode, _, stderr = self.run_cli(authorize)
        self.assertEqual(returncode, 0, stderr)

        submit = self.handoff_request(
            topic,
            operation="submit-child-result",
            ledger_revision=5,
            owner_ref=child_ref,
            handoff_id=prepared["handoff_id"],
            attempt_id=prepared["attempt_id"],
            result_scope=["api"],
            summary="Use typed request objects.",
        )
        submit["actor_topic_id"] = prepared["target_topic_id"]
        returncode, claimed, stderr = self.run_cli(submit)
        self.assertEqual(returncode, 0, stderr)
        self.assertEqual(claimed["state"], "pending-parent-acceptance")
        returncode, absorbed, stderr = self.run_cli(
            self.handoff_request(
                topic,
                operation="record-child-result",
                ledger_revision=6,
                handoff_id=prepared["handoff_id"],
                child_result_id=claimed["child_result_id"],
                effect="absorb",
            )
        )
        self.assertEqual(returncode, 0, stderr)
        self.assertEqual(absorbed["state"], "absorbed")
        duplicate = self.handoff_request(
            topic,
            operation="record-child-result",
            ledger_revision=7,
            handoff_id=prepared["handoff_id"],
            child_result_id=claimed["child_result_id"],
            effect="impact",
        )
        returncode, rejected, _ = self.run_cli(duplicate)
        self.assertEqual(returncode, 1)
        self.assertEqual(rejected["error"]["code"], "child_result_state_conflict")

        submit = self.handoff_request(
            topic,
            operation="submit-child-result",
            ledger_revision=7,
            owner_ref=child_ref,
            handoff_id=prepared["handoff_id"],
            attempt_id=prepared["attempt_id"],
            result_scope=["storage"],
            summary="Change the parent ledger format.",
        )
        submit["actor_topic_id"] = prepared["target_topic_id"]
        returncode, claimed, stderr = self.run_cli(submit)
        self.assertEqual(returncode, 0, stderr)
        returncode, impact, stderr = self.run_cli(
            self.handoff_request(
                topic,
                operation="record-child-result",
                ledger_revision=8,
                handoff_id=prepared["handoff_id"],
                child_result_id=claimed["child_result_id"],
                effect="impact",
            )
        )
        self.assertEqual(returncode, 0, stderr)
        self.assertEqual(impact["state"], "pending-impact")
        self.assertRegex(str(impact["impact_id"]), r"^IMP-[0-9a-f]{32}$")

    def test_handoff_allowlists_and_explicit_failure_reconciliation_are_recoverable(self) -> None:
        project = self.make_project("handoff-explicit-recovery", git=False)
        topic = self.bootstrap_topic(project)
        invalid = self.handoff_request(
            topic,
            operation="prepare-handoff",
            ledger_revision=1,
            handoff_kind="child",
            target_slug="bad-envelope",
            scope=["api"],
            work_snapshot={"goal": "Choose the API.", "conversation_history": ["secret"]},
            authoritative_references=[],
        )
        returncode, rejected, _ = self.run_cli(invalid)
        self.assertEqual(returncode, 1)
        self.assertEqual(rejected["error"]["code"], "invalid_request")

        prepared = self.prepare_child_handoff(topic)
        returncode, failed, stderr = self.run_cli(
            self.handoff_request(
                topic,
                operation="record-handoff-failure",
                ledger_revision=2,
                handoff_id=prepared["handoff_id"],
                attempt_id=prepared["attempt_id"],
                reason="task creation was explicitly rejected",
            )
        )
        self.assertEqual(returncode, 0, stderr)
        self.assertEqual(failed["state"], "failed")
        returncode, retried, stderr = self.run_cli(
            self.handoff_request(
                topic,
                operation="retry-handoff",
                ledger_revision=3,
                handoff_id=prepared["handoff_id"],
                prior_attempt_id=prepared["attempt_id"],
                forced=False,
            )
        )
        self.assertEqual(returncode, 0, stderr)
        self.assertTrue(retried["attempt_id"].endswith("-2"))

        returncode, unknown, stderr = self.run_cli(
            self.handoff_request(
                topic,
                operation="record-handoff-outcome-unknown",
                ledger_revision=4,
                handoff_id=prepared["handoff_id"],
                attempt_id=retried["attempt_id"],
            )
        )
        self.assertEqual(returncode, 0, stderr)
        self.assertEqual(unknown["state"], "outcome-unknown")
        returncode, reconciled, stderr = self.run_cli(
            self.handoff_request(
                topic,
                operation="reconcile-handoff-attempt",
                ledger_revision=5,
                handoff_id=prepared["handoff_id"],
                attempt_id=retried["attempt_id"],
                outcome="not-created",
                reason="provider confirmed no task exists",
            )
        )
        self.assertEqual(returncode, 0, stderr)
        self.assertEqual(reconciled["state"], "failed")
        self.assertFalse(reconciled["binding_eligible"])

    def test_binding_failure_before_ledger_commit_leaves_no_partial_claim(self) -> None:
        project = self.make_project("handoff-binding-fault", git=False)
        topic = self.bootstrap_topic(project)
        prepared = self.prepare_child_handoff(topic)
        bind = self.handoff_request(
            topic,
            operation="bind-handoff",
            ledger_revision=2,
            handoff_id=prepared["handoff_id"],
            attempt_id=prepared["attempt_id"],
            conversation_ref="codex-thread:faulted-child",
            verified_identity={
                "project_id": topic["project_id"],
                "tree_id": topic["tree_id"],
                "topic_id": prepared["target_topic_id"],
                "handoff_id": prepared["handoff_id"],
                "attempt_id": prepared["attempt_id"],
                "payload_sha256": prepared["payload_sha256"],
            },
        )
        returncode, failed, _ = self.run_cli(
            bind, failpoint="handoff-before-binding-ledger-write"
        )
        self.assertEqual(returncode, 1)
        self.assertEqual(failed["error"]["code"], "injected_failure")

        returncode, before_retry, stderr = self.run_cli(
            self.handoff_request(
                topic, operation="read-handoff", handoff_id=prepared["handoff_id"]
            )
        )
        self.assertEqual(returncode, 0, stderr)
        self.assertEqual(before_retry["attempts"][0]["state"], "setup-pending")
        self.assertEqual(before_retry["bindings"], [])

        bind["idempotency_key"] = str(uuid.uuid4())
        returncode, bound, stderr = self.run_cli(bind)
        self.assertEqual(returncode, 0, stderr)
        self.assertEqual(bound["state"], "bound-pending-acceptance")
        returncode, final, stderr = self.run_cli(
            self.handoff_request(
                topic, operation="read-handoff", handoff_id=prepared["handoff_id"]
            )
        )
        self.assertEqual(returncode, 0, stderr)
        active = [item for item in final["bindings"] if item["binding_state"] == "active"]
        self.assertEqual(len(active), 1)

    def prepare_checkpoint(
        self,
        topic: dict[str, object],
        *,
        ledger_revision: int,
        topic_revision: int = 1,
        purpose: str = "pause",
        base_ref: str = "HEAD",
    ) -> dict[str, object]:
        returncode, prepared, stderr = self.run_cli(
            self.checkpoint_request(
                topic,
                operation="prepare-checkpoint",
                ledger_revision=ledger_revision,
                topic_revision=topic_revision,
                purpose=purpose,
                base_ref=base_ref,
            )
        )
        self.assertEqual(returncode, 0, stderr)
        return prepared

    def publish_git_checkpoint(
        self,
        project: Path,
        topic: dict[str, object],
        prepared: dict[str, object],
        *,
        ledger_revision: int,
    ) -> dict[str, object]:
        lease = self.acquire_repository_coordination_lease(project)
        holder = lease["holder"]
        assert isinstance(holder, dict)
        returncode, published, stderr = self.run_cli(
            self.checkpoint_request(
                topic,
                operation="publish-git-checkpoint",
                ledger_revision=ledger_revision,
                checkpoint_id=prepared["checkpoint_id"],
                expected_checkpoint_revision=prepared["checkpoint_record_revision"],
                repository_coordination_lease={
                    "path": lease["path"],
                    "lease_id": holder["lease_id"],
                    "version": lease["version"],
                },
            )
        )
        self.assertEqual(returncode, 0, stderr)
        self.supervision_cli(
            "release-repository-coordination-lease",
            "--file",
            str(lease["path"]),
            "--id",
            str(holder["lease_id"]),
            "--version",
            str(lease["version"]),
        )
        return published

    def create_matching_checkpoint_commit(
        self,
        project: Path,
        prepared: dict[str, object],
        *,
        timestamp: str,
        parent_commit: str | None = None,
    ) -> str:
        path = str(prepared["paths"][0])
        document = (project / path).read_bytes()
        blob_id = subprocess.run(
            ["git", "-C", str(project), "hash-object", "-w", "--stdin"],
            input=document,
            check=True,
            stdout=subprocess.PIPE,
        ).stdout.decode("ascii").strip()
        index_path = self.root / f"index-{uuid.uuid4().hex}"
        environment = dict(os.environ)
        environment["GIT_INDEX_FILE"] = str(index_path)
        parent = parent_commit or str(prepared["base_commit"])
        subprocess.run(
            ["git", "-C", str(project), "read-tree", parent],
            check=True,
            env=environment,
        )
        subprocess.run(
            ["git", "-C", str(project), "update-index", "--add", "--cacheinfo", "100644", blob_id, path],
            check=True,
            env=environment,
        )
        tree_id = subprocess.run(
            ["git", "-C", str(project), "write-tree"],
            check=True,
            env=environment,
            stdout=subprocess.PIPE,
            text=True,
        ).stdout.strip()
        message = (
            f"discussion checkpoint: {prepared['purpose']}\n\n"
            f"Codex-Discussion-Checkpoint: {prepared['checkpoint_id']}\n"
            f"Codex-Document-SHA256: {prepared['document_digests'][path]}\n"
            f"Codex-Discussion-Decision-SHA256: {prepared['decision_digest']}\n"
            f"Codex-Discussion-Paths-SHA256: {prepared['path_set_digest']}\n"
        )
        environment.update(
            {
                "GIT_AUTHOR_NAME": "Checkpoint Test",
                "GIT_AUTHOR_EMAIL": "checkpoint@example.com",
                "GIT_AUTHOR_DATE": timestamp,
                "GIT_COMMITTER_NAME": "Checkpoint Test",
                "GIT_COMMITTER_EMAIL": "checkpoint@example.com",
                "GIT_COMMITTER_DATE": timestamp,
            }
        )
        return subprocess.run(
            [
                "git", "-C", str(project), "commit-tree", tree_id, "-p",
                parent,
            ],
            input=message,
            check=True,
            env=environment,
            stdout=subprocess.PIPE,
            text=True,
        ).stdout.strip()

    def complete_update(
        self,
        project: Path,
        topic: dict[str, object],
        *,
        ledger_revision: int,
        topic_revision: int,
        mutation: dict[str, object],
        owner_ref: str = "discussion-task",
    ) -> tuple[dict[str, object], int, int]:
        returncode, prepared, stderr = self.run_cli(
            self.evolution_request(
                topic,
                operation="prepare-topic-update",
                expected_revision=ledger_revision,
                expected_topic_revision=topic_revision,
                owner_ref=owner_ref,
                mutation=mutation,
            )
        )
        self.assertEqual(returncode, 0, stderr)
        lease = self.acquire_document_lease(project, owner_ref=owner_ref)
        holder = lease["holder"]
        assert isinstance(holder, dict)
        returncode, applied, stderr = self.run_cli(
            self.evolution_request(
                topic,
                operation="apply-document-write",
                expected_revision=ledger_revision + 1,
                expected_topic_revision=topic_revision + 1,
                owner_ref=owner_ref,
                document_write_id=prepared["document_write_id"],
                document_lease={
                    "path": lease["path"],
                    "lease_id": holder["lease_id"],
                    "version": lease["version"],
                },
            )
        )
        self.assertEqual(returncode, 0, stderr)
        self.assertTrue(applied["document_verified"])
        released = self.release_document_lease(lease)
        returncode, completed, stderr = self.run_cli(
            self.evolution_request(
                topic,
                operation="complete-document-write",
                expected_revision=ledger_revision + 2,
                expected_topic_revision=topic_revision + 1,
                owner_ref=owner_ref,
                document_write_id=prepared["document_write_id"],
                document_lease_release={
                    "path": released["path"],
                    "lease_id": released["released_lease_id"],
                    "version": released["version"],
                },
            )
        )
        self.assertEqual(returncode, 0, stderr)
        self.assertTrue(completed["release_verified"])
        return prepared, ledger_revision + 3, topic_revision + 1

    def test_confirmed_decision_is_applied_with_immutable_dw_and_verified_lease(
        self,
    ) -> None:
        project = self.make_project("decision-write", git=True)
        topic = self.bootstrap_topic(project)
        prepare_request = self.evolution_request(
            topic,
            operation="prepare-topic-update",
            expected_revision=1,
            mutation={
                "type": "confirm-decision",
                "summary": "Use a single durable ledger.",
                "rationale": "It prevents competing coordination authorities.",
            },
        )
        returncode, prepared, stderr = self.run_cli(prepare_request)
        self.assertEqual(returncode, 0, stderr)
        self.assertEqual(prepared["state"], "confirmed-but-pending")
        self.assertRegex(str(prepared["decision_id"]), r"^D-[0-9a-f]{32}$")
        self.assertRegex(str(prepared["document_write_id"]), r"^DW-[0-9a-f]{32}$")
        self.assertNotEqual(prepared["before_sha256"], prepared["after_sha256"])
        payload_path = Path(str(prepared["payload_path"]))
        payload_before = payload_path.read_bytes()

        lease = self.acquire_document_lease(project)
        holder = lease["holder"]
        assert isinstance(holder, dict)
        apply_request = self.evolution_request(
            topic,
            operation="apply-document-write",
            expected_revision=2,
            expected_topic_revision=2,
            document_write_id=prepared["document_write_id"],
            document_lease={
                "path": lease["path"],
                "lease_id": holder["lease_id"],
                "version": lease["version"],
            },
        )
        returncode, applied, stderr = self.run_cli(apply_request)
        self.assertEqual(returncode, 0, stderr)
        self.assertEqual(applied["state"], "applied-pending-release")
        self.assertTrue(applied["document_verified"])
        self.assertEqual(payload_path.read_bytes(), payload_before)
        topic_text = Path(str(topic["topic_document_path"])).read_text(encoding="utf-8")
        self.assertIn(str(prepared["decision_id"]), topic_text)
        self.assertIn("Use a single durable ledger.", topic_text)

        released = self.release_document_lease(lease)
        complete_request = self.evolution_request(
            topic,
            operation="complete-document-write",
            expected_revision=3,
            expected_topic_revision=2,
            document_write_id=prepared["document_write_id"],
            document_lease_release={
                "path": released["path"],
                "lease_id": released["released_lease_id"],
                "version": released["version"],
            },
        )
        returncode, completed, stderr = self.run_cli(complete_request)
        self.assertEqual(returncode, 0, stderr)
        self.assertEqual(completed["state"], "completed")
        self.assertTrue(completed["release_verified"])

        returncode, inspected, stderr = self.run_cli(
            self.evolution_request(
                topic,
            operation="read-topic",
            )
        )
        self.assertEqual(returncode, 0, stderr)
        self.assertEqual(inspected["decisions"][0]["decision_id"], prepared["decision_id"])
        self.assertEqual(inspected["pending_document_writes"][0]["state"], "completed")

    def test_pending_update_replay_is_idempotent_and_blocks_new_substantive_update(
        self,
    ) -> None:
        project = self.make_project("pending-replay", git=True)
        topic = self.bootstrap_topic(project)
        request = self.evolution_request(
            topic,
            operation="prepare-topic-update",
            expected_revision=1,
            mutation={
                "type": "confirm-decision",
                "summary": "Preserve the checkpoint.",
                "rationale": "Uncertain persistence must be recoverable.",
            },
        )
        first_code, first, first_stderr = self.run_cli(request)
        second_code, second, second_stderr = self.run_cli(request)
        self.assertEqual(first_code, 0, first_stderr)
        self.assertEqual(second_code, 0, second_stderr)
        self.assertFalse(first["idempotent_replay"])
        self.assertTrue(second["idempotent_replay"])
        self.assertEqual(first["document_write_id"], second["document_write_id"])
        self.assertEqual(first["ledger_revision"], second["ledger_revision"])

        returncode, blocked, _ = self.run_cli(
            self.evolution_request(
                topic,
                operation="prepare-topic-update",
                expected_revision=2,
                expected_topic_revision=2,
                mutation={
                    "type": "set-active-question",
                    "prompt": "Can discussion continue?",
                    "recommendation": "Reconcile first.",
                    "reason": "The confirmed write is still pending.",
                },
            )
        )
        self.assertEqual(returncode, 1)
        self.assertEqual(blocked["error"]["code"], "document_write_reconciliation_required")
        self.assertEqual(blocked["state"], "confirmed-but-pending")

        returncode, reconciled, stderr = self.run_cli(
            self.evolution_request(
                topic,
                operation="reconcile-document-write",
                expected_revision=2,
                expected_topic_revision=2,
                document_write_id=first["document_write_id"],
            )
        )
        self.assertEqual(returncode, 0, stderr)
        self.assertEqual(reconciled["state"], "confirmed-but-pending")
        self.assertFalse(reconciled["document_verified"])

    def test_ownership_conflict_and_stale_lease_cannot_apply_pending_write(self) -> None:
        project = self.make_project("write-authority", git=True)
        topic = self.bootstrap_topic(project)
        returncode, prepared, stderr = self.run_cli(
            self.evolution_request(
                topic,
                operation="prepare-topic-update",
                expected_revision=1,
                mutation={
                    "type": "confirm-decision",
                    "summary": "Require both authorities.",
                    "rationale": "A lease alone must not grant topic ownership.",
                },
            )
        )
        self.assertEqual(returncode, 0, stderr)

        foreign_lease = self.acquire_document_lease(project, owner_ref="foreign-task")
        foreign_holder = foreign_lease["holder"]
        assert isinstance(foreign_holder, dict)
        returncode, conflict, _ = self.run_cli(
            self.evolution_request(
                topic,
                operation="apply-document-write",
                expected_revision=2,
                expected_topic_revision=2,
                owner_ref="foreign-task",
                document_write_id=prepared["document_write_id"],
                document_lease={
                    "path": foreign_lease["path"],
                    "lease_id": foreign_holder["lease_id"],
                    "version": foreign_lease["version"],
                },
            )
        )
        self.assertEqual(returncode, 1)
        self.assertEqual(conflict["error"]["code"], "document_ownership_conflict")
        self.release_document_lease(foreign_lease)

        stale_lease = self.acquire_document_lease(project)
        stale_holder = stale_lease["holder"]
        assert isinstance(stale_holder, dict)
        self.release_document_lease(stale_lease)
        returncode, stale, _ = self.run_cli(
            self.evolution_request(
                topic,
                operation="apply-document-write",
                expected_revision=2,
                expected_topic_revision=2,
                document_write_id=prepared["document_write_id"],
                document_lease={
                    "path": stale_lease["path"],
                    "lease_id": stale_holder["lease_id"],
                    "version": stale_lease["version"],
                },
            )
        )
        self.assertEqual(returncode, 1)
        self.assertEqual(stale["error"]["code"], "document_lease_invalid")

    def test_validate_rejects_damaged_and_orphaned_pending_write_payloads(self) -> None:
        for damage_kind in ("damaged", "orphaned"):
            with self.subTest(damage_kind=damage_kind):
                project = self.make_project(f"payload-{damage_kind}", git=True)
                topic = self.bootstrap_topic(project)
                returncode, prepared, stderr = self.run_cli(
                    self.evolution_request(
                        topic,
                        operation="prepare-topic-update",
                        expected_revision=1,
                        mutation={
                            "type": "confirm-decision",
                            "summary": "Keep immutable payloads.",
                            "rationale": "Recovery needs exact bytes.",
                        },
                    )
                )
                self.assertEqual(returncode, 0, stderr)
                payload_path = Path(str(prepared["payload_path"]))
                if damage_kind == "damaged":
                    payload_path.chmod(0o600)
                    payload_path.write_bytes(b"damaged\n")
                else:
                    (payload_path.parent / "DW-orphan.payload").write_bytes(b"orphan\n")
                returncode, response, _ = self.run_cli(
                    self.evolution_request(topic, operation="validate")
                )
                self.assertEqual(returncode, 1)
                expected = (
                    "document_write_payload_damaged"
                    if damage_kind == "damaged"
                    else "orphaned_document_write"
                )
                self.assertEqual(response["error"]["code"], expected)

    def test_reconcile_adopts_uncertain_apply_and_completes_after_release(self) -> None:
        project = self.make_project("reconcile-uncertain", git=True)
        topic = self.bootstrap_topic(project)
        returncode, prepared, stderr = self.run_cli(
            self.evolution_request(
                topic,
                operation="prepare-topic-update",
                expected_revision=1,
                mutation={
                    "type": "confirm-decision",
                    "summary": "Recover outcome-unknown writes.",
                    "rationale": "The durable checkpoint must survive caller uncertainty.",
                },
            )
        )
        self.assertEqual(returncode, 0, stderr)
        lease = self.acquire_document_lease(project)
        holder = lease["holder"]
        assert isinstance(holder, dict)
        Path(str(topic["topic_document_path"])).write_bytes(
            Path(str(prepared["payload_path"])).read_bytes()
        )

        returncode, adopted, stderr = self.run_cli(
            self.evolution_request(
                topic,
                operation="reconcile-document-write",
                expected_revision=2,
                expected_topic_revision=2,
                document_write_id=prepared["document_write_id"],
            )
        )
        self.assertEqual(returncode, 0, stderr)
        self.assertEqual(adopted["state"], "applied-pending-release")
        self.assertTrue(adopted["document_verified"])

        self.release_document_lease(lease)
        returncode, completed, stderr = self.run_cli(
            self.evolution_request(
                topic,
                operation="reconcile-document-write",
                expected_revision=3,
                expected_topic_revision=2,
                document_write_id=prepared["document_write_id"],
            )
        )
        self.assertEqual(returncode, 0, stderr)
        self.assertEqual(completed["state"], "completed")
        self.assertTrue(completed["release_verified"])

    def test_non_git_topic_update_uses_shared_codex_document_lease(self) -> None:
        project = self.make_project("non-git-update", git=False)
        topic = self.bootstrap_topic(project)
        prepared, ledger_revision, topic_revision = self.complete_update(
            project,
            topic,
            ledger_revision=1,
            topic_revision=1,
            mutation={
                "type": "confirm-decision",
                "summary": "Support non-Git topics.",
                "rationale": "Topic evolution is project-agnostic.",
            },
        )
        self.assertRegex(str(prepared["decision_id"]), r"^D-[0-9a-f]{32}$")
        self.assertEqual((project / ".codex" / "cc-switch-document-lease.json").is_file(), True)
        returncode, response, stderr = self.run_cli(
            self.evolution_request(topic, operation="read-topic")
        )
        self.assertEqual(returncode, 0, stderr)
        self.assertEqual(response["ledger_revision"], ledger_revision)
        self.assertEqual(response["record_revision"], topic_revision)

    def test_inserted_idea_suspends_then_adjusts_the_only_active_question(self) -> None:
        project = self.make_project("inserted-idea", git=True)
        topic = self.bootstrap_topic(project)
        question, ledger_revision, topic_revision = self.complete_update(
            project,
            topic,
            ledger_revision=1,
            topic_revision=1,
            mutation={
                "type": "set-active-question",
                "prompt": "Which storage layout should we choose?",
                "recommendation": "Use one tree ledger.",
                "reason": "It centralizes coordination state.",
            },
        )
        idea, ledger_revision, topic_revision = self.complete_update(
            project,
            topic,
            ledger_revision=ledger_revision,
            topic_revision=topic_revision,
            mutation={"type": "insert-idea", "summary": "Support non-Git projects too."},
        )
        self.assertEqual(idea["suspended_question_id"], question["question_id"])
        returncode, suspended, stderr = self.run_cli(
            self.evolution_request(topic, operation="read-topic")
        )
        self.assertEqual(returncode, 0, stderr)
        self.assertIsNone(suspended["active_question"])
        self.assertEqual(suspended["questions"][0]["state"], "suspended")

        _, ledger_revision, topic_revision = self.complete_update(
            project,
            topic,
            ledger_revision=ledger_revision,
            topic_revision=topic_revision,
            mutation={
                "type": "resolve-inserted-idea",
                "question_id": question["question_id"],
                "action": "adjust",
                "adjusted_prompt": "Which storage layout works for Git and non-Git projects?",
            },
        )
        returncode, resumed, stderr = self.run_cli(
            self.evolution_request(topic, operation="read-topic")
        )
        self.assertEqual(returncode, 0, stderr)
        self.assertEqual(resumed["active_question"]["state"], "active")
        self.assertIn("Git and non-Git", resumed["active_question"]["prompt"])

    def test_changed_direction_requires_separate_resolution_for_each_decision(self) -> None:
        project = self.make_project("decision-impacts", git=True)
        topic = self.bootstrap_topic(project)
        ledger_revision = topic_revision = 1
        decisions: list[str] = []
        for index in range(4):
            prepared, ledger_revision, topic_revision = self.complete_update(
                project,
                topic,
                ledger_revision=ledger_revision,
                topic_revision=topic_revision,
                mutation={
                    "type": "confirm-decision",
                    "summary": f"Decision {index + 1}",
                    "rationale": "Initial direction.",
                },
            )
            decisions.append(str(prepared["decision_id"]))
        changed, ledger_revision, topic_revision = self.complete_update(
            project,
            topic,
            ledger_revision=ledger_revision,
            topic_revision=topic_revision,
            mutation={
                "type": "change-direction",
                "summary": "Adopt the new direction.",
                "affected_decision_ids": decisions,
            },
        )
        impact_ids = changed["impact_ids"]
        self.assertEqual(len(impact_ids), 4)
        actions = ["keep", "adjust", "replace", "discard"]
        for index, action in enumerate(actions):
            _, ledger_revision, topic_revision = self.complete_update(
                project,
                topic,
                ledger_revision=ledger_revision,
                topic_revision=topic_revision,
                mutation={
                    "type": "resolve-impact",
                    "impact_id": impact_ids[index],
                    "decision_id": decisions[index],
                    "action": action,
                    "summary": f"{action} resolution",
                },
            )
            returncode, current, stderr = self.run_cli(
                self.evolution_request(topic, operation="read-topic")
            )
            self.assertEqual(returncode, 0, stderr)
            resolved = [item for item in current["impacts"] if item["state"] == "resolved"]
            self.assertEqual(len(resolved), index + 1)
        self.assertEqual(
            [item["action"] for item in current["impacts"]], actions
        )

    def test_git_checkpoint_freezes_and_publishes_exact_document_commit(self) -> None:
        project = self.make_project("git-checkpoint", git=True)
        (project / "unrelated.txt").write_text("keep me\n", encoding="utf-8")
        subprocess.run(
            ["git", "-C", str(project), "add", "unrelated.txt"], check=True
        )
        subprocess.run(
            [
                "git", "-C", str(project), "-c", "user.name=Test", "-c",
                "user.email=test@example.com", "commit", "-qm", "base",
            ],
            check=True,
        )
        topic = self.bootstrap_topic(project)
        index_before = subprocess.run(
            ["git", "-C", str(project), "write-tree"],
            check=True, stdout=subprocess.PIPE, text=True,
        ).stdout.strip()
        head_before = subprocess.run(
            ["git", "-C", str(project), "rev-parse", "HEAD"],
            check=True, stdout=subprocess.PIPE, text=True,
        ).stdout.strip()
        prepared = self.prepare_checkpoint(topic, ledger_revision=1)
        self.assertEqual(prepared["base_commit"], head_before)
        self.assertEqual(prepared["paths"], ["docs/discussions/checkout-redesign/topic.md"])
        published = self.publish_git_checkpoint(
            project, topic, prepared, ledger_revision=2
        )
        self.assertEqual(published["state"], "completed")
        commit_id = str(published["commit_id"])
        message = subprocess.run(
            ["git", "-C", str(project), "show", "-s", "--format=%B", commit_id],
            check=True, stdout=subprocess.PIPE, text=True,
        ).stdout
        self.assertIn(
            f"Codex-Discussion-Checkpoint: {prepared['checkpoint_id']}", message
        )
        self.assertIn(
            "Codex-Document-SHA256: "
            + str(prepared["document_digests"][prepared["paths"][0]]),
            message,
        )
        changed_paths = subprocess.run(
            [
                "git", "-C", str(project), "diff-tree", "--no-commit-id",
                "--name-only", "-r", commit_id,
            ],
            check=True, stdout=subprocess.PIPE, text=True,
        ).stdout.splitlines()
        self.assertEqual(changed_paths, prepared["paths"])
        self.assertEqual(
            subprocess.run(
                ["git", "-C", str(project), "rev-parse", "HEAD"],
                check=True, stdout=subprocess.PIPE, text=True,
            ).stdout.strip(),
            head_before,
        )
        self.assertEqual(
            subprocess.run(
                ["git", "-C", str(project), "write-tree"],
                check=True, stdout=subprocess.PIPE, text=True,
            ).stdout.strip(),
            index_before,
        )
        self.assertEqual(
            subprocess.run(
                [
                    "git", "-C", str(project), "rev-parse",
                    str(published["checkpoint_ref"]),
                ],
                check=True,
                stdout=subprocess.PIPE,
                text=True,
            ).stdout.strip(),
            commit_id,
        )
        self.assertEqual(
            (project / "unrelated.txt").read_text(encoding="utf-8"), "keep me\n"
        )
        returncode, validated, stderr = self.run_cli(
            self.checkpoint_request(topic, operation="validate")
        )
        self.assertEqual(returncode, 0, stderr)
        self.assertEqual(validated["checkpoint_count"], 1)

    def test_checkpoint_publication_rejects_stale_decision_digest(self) -> None:
        for git in (True, False):
            with self.subTest(storage_kind="git" if git else "non-git"):
                project = self.make_project(f"stale-decision-{'git' if git else 'snapshot'}", git=git)
                if git:
                    (project / "base.txt").write_text("base\n", encoding="utf-8")
                    subprocess.run(
                        ["git", "-C", str(project), "add", "base.txt"], check=True
                    )
                    subprocess.run(
                        [
                            "git", "-C", str(project), "-c", "user.name=Test",
                            "-c", "user.email=test@example.com", "commit", "-qm", "base",
                        ],
                        check=True,
                    )
                topic = self.bootstrap_topic(project)
                prepared = self.prepare_checkpoint(
                    topic,
                    ledger_revision=1,
                    base_ref="HEAD" if git else "project-root",
                )
                returncode, update, stderr = self.run_cli(
                    self.evolution_request(
                        topic,
                        operation="prepare-topic-update",
                        expected_revision=2,
                        expected_topic_revision=1,
                        mutation={
                            "type": "confirm-decision",
                            "summary": "Publish only the current decision digest.",
                            "rationale": "A checkpoint must not freeze a stale authority.",
                        },
                    )
                )
                self.assertEqual(returncode, 0, stderr)
                self.assertEqual(update["record_revision"], 2)
                self.assertEqual(
                    hashlib.sha256(
                        Path(str(topic["topic_document_path"])).read_bytes()
                    ).hexdigest(),
                    prepared["document_digests"][prepared["paths"][0]],
                )

                publish_parameters: dict[str, object] = {}
                lease: dict[str, object] | None = None
                if git:
                    lease = self.acquire_repository_coordination_lease(project)
                    holder = lease["holder"]
                    assert isinstance(holder, dict)
                    publish_parameters["repository_coordination_lease"] = {
                        "path": lease["path"],
                        "lease_id": holder["lease_id"],
                        "version": lease["version"],
                    }
                returncode, rejected, publish_stderr = self.run_cli(
                    self.checkpoint_request(
                        topic,
                        operation=(
                            "publish-git-checkpoint"
                            if git
                            else "publish-non-git-checkpoint"
                        ),
                        ledger_revision=3,
                        topic_revision=2,
                        checkpoint_id=prepared["checkpoint_id"],
                        expected_checkpoint_revision=1,
                        **publish_parameters,
                    )
                )
                if lease is not None:
                    holder = lease["holder"]
                    assert isinstance(holder, dict)
                    self.supervision_cli(
                        "release-repository-coordination-lease",
                        "--file", str(lease["path"]),
                        "--id", str(holder["lease_id"]),
                        "--version", str(lease["version"]),
                    )
                self.assertEqual(returncode, 1, publish_stderr)
                self.assertEqual(
                    rejected["error"]["code"], "checkpoint_changed_draft"
                )

    def test_checkpoint_publication_conflict_reports_current_authority(self) -> None:
        for git in (True, False):
            with self.subTest(storage_kind="git" if git else "non-git"):
                project = self.make_project(f"checkpoint-conflict-{'git' if git else 'snapshot'}", git=git)
                if git:
                    (project / "base.txt").write_text("base\n", encoding="utf-8")
                    subprocess.run(
                        ["git", "-C", str(project), "add", "base.txt"], check=True
                    )
                    subprocess.run(
                        [
                            "git", "-C", str(project), "-c", "user.name=Test",
                            "-c", "user.email=test@example.com", "commit", "-qm", "base",
                        ],
                        check=True,
                    )
                topic = self.bootstrap_topic(project)
                prepared = self.prepare_checkpoint(
                    topic,
                    ledger_revision=1,
                    base_ref="HEAD" if git else "project-root",
                )
                publish_parameters: dict[str, object] = {}
                lease: dict[str, object] | None = None
                if git:
                    lease = self.acquire_repository_coordination_lease(project)
                    holder = lease["holder"]
                    assert isinstance(holder, dict)
                    publish_parameters["repository_coordination_lease"] = {
                        "path": lease["path"],
                        "lease_id": holder["lease_id"],
                        "version": lease["version"],
                    }
                returncode, conflict, stderr = self.run_cli(
                    self.checkpoint_request(
                        topic,
                        operation=(
                            "publish-git-checkpoint"
                            if git
                            else "publish-non-git-checkpoint"
                        ),
                        ledger_revision=2,
                        checkpoint_id=prepared["checkpoint_id"],
                        expected_checkpoint_revision=999,
                        **publish_parameters,
                    )
                )
                if lease is not None:
                    holder = lease["holder"]
                    assert isinstance(holder, dict)
                    self.supervision_cli(
                        "release-repository-coordination-lease",
                        "--file", str(lease["path"]),
                        "--id", str(holder["lease_id"]),
                        "--version", str(lease["version"]),
                    )
                self.assertEqual(returncode, 1, stderr)
                self.assertEqual(
                    conflict["error"]["code"], "checkpoint_identity_conflict"
                )
                self.assertEqual(conflict["state"], "prepared")
                self.assertEqual(conflict["ledger_revision"], 2)
                self.assertEqual(conflict["record_revision"], 1)
                self.assertEqual(conflict["project_id"], topic["project_id"])
                self.assertEqual(conflict["tree_id"], topic["tree_id"])
                self.assertEqual(conflict["topic_id"], topic["topic_id"])
                self.assertEqual(
                    conflict["checkpoint_id"], prepared["checkpoint_id"]
                )

    def test_changed_draft_cancels_identity_and_uncertain_checkpoint_blocks_new_intent(
        self,
    ) -> None:
        project = self.make_project("checkpoint-intents", git=True)
        (project / "base.txt").write_text("base\n", encoding="utf-8")
        subprocess.run(["git", "-C", str(project), "add", "base.txt"], check=True)
        subprocess.run(
            [
                "git", "-C", str(project), "-c", "user.name=Test", "-c",
                "user.email=test@example.com", "commit", "-qm", "base",
            ],
            check=True,
        )
        topic = self.bootstrap_topic(project)
        first = self.prepare_checkpoint(topic, ledger_revision=1)
        Path(str(topic["topic_document_path"])).write_text("changed\n", encoding="utf-8")
        returncode, cancelled, stderr = self.run_cli(
            self.checkpoint_request(
                topic,
                operation="cancel-checkpoint",
                ledger_revision=2,
                checkpoint_id=first["checkpoint_id"],
                reason="draft changed",
            )
        )
        self.assertEqual(returncode, 0, stderr)
        self.assertTrue(cancelled["draft_changed"])
        self.assertFalse(cancelled["identity_reusable"])
        second = self.prepare_checkpoint(topic, ledger_revision=3)
        self.assertNotEqual(second["checkpoint_id"], first["checkpoint_id"])
        returncode, unknown, stderr = self.run_cli(
            self.checkpoint_request(
                topic,
                operation="record-checkpoint-outcome-unknown",
                ledger_revision=4,
                checkpoint_id=second["checkpoint_id"],
                expected_checkpoint_revision=1,
            )
        )
        self.assertEqual(returncode, 0, stderr)
        self.assertEqual(unknown["state"], "outcome-unknown")
        returncode, blocked, _ = self.run_cli(
            self.checkpoint_request(
                topic,
                operation="prepare-checkpoint",
                ledger_revision=5,
                purpose="handoff",
                base_ref="HEAD",
            )
        )
        self.assertEqual(returncode, 1)
        self.assertEqual(
            blocked["error"]["code"], "checkpoint_reconciliation_required"
        )
        returncode, reconciled, stderr = self.run_cli(
            self.checkpoint_request(
                topic,
                operation="reconcile-git-checkpoint",
                ledger_revision=5,
                checkpoint_id=second["checkpoint_id"],
                expected_checkpoint_revision=2,
            )
        )
        self.assertEqual(returncode, 0, stderr)
        self.assertEqual(reconciled["state"], "prepared")
        self.assertTrue(reconciled["retry_allowed"])

    def test_uncertain_git_commit_is_adopted_only_after_full_unique_match(self) -> None:
        project = self.make_project("git-reconcile", git=True)
        (project / "base.txt").write_text("base\n", encoding="utf-8")
        subprocess.run(["git", "-C", str(project), "add", "base.txt"], check=True)
        subprocess.run(
            [
                "git", "-C", str(project), "-c", "user.name=Test", "-c",
                "user.email=test@example.com", "commit", "-qm", "base",
            ],
            check=True,
        )
        topic = self.bootstrap_topic(project)
        prepared = self.prepare_checkpoint(topic, ledger_revision=1)
        published = self.publish_git_checkpoint(
            project, topic, prepared, ledger_revision=2
        )
        commit_id = str(published["commit_id"])
        returncode, active, stderr = self.run_cli(
            self.checkpoint_request(
                topic,
                operation="register-active-checkpoint-source",
                ledger_revision=3,
                checkpoint_id=prepared["checkpoint_id"],
                expected_checkpoint_revision=2,
                implementation_id="implementation-1",
            )
        )
        self.assertEqual(returncode, 0, stderr)
        self.assertEqual(active["state"], "active")
        rewritten_parent_tree = subprocess.run(
            ["git", "-C", str(project), "show", "-s", "--format=%T", "HEAD"],
            check=True,
            stdout=subprocess.PIPE,
            text=True,
        ).stdout.strip()
        rewritten_parent = subprocess.run(
            ["git", "-C", str(project), "commit-tree", rewritten_parent_tree],
            input="rewritten base\n",
            check=True,
            stdout=subprocess.PIPE,
            text=True,
            env={
                **os.environ,
                "GIT_AUTHOR_NAME": "Test",
                "GIT_AUTHOR_EMAIL": "test@example.com",
                "GIT_COMMITTER_NAME": "Test",
                "GIT_COMMITTER_EMAIL": "test@example.com",
            },
        ).stdout.strip()
        replacement_commit = self.create_matching_checkpoint_commit(
            project,
            prepared,
            timestamp="2026-02-01T00:00:00+00:00",
            parent_commit=rewritten_parent,
        )
        returncode, broken, stderr = self.run_cli(
            self.checkpoint_request(
                topic,
                operation="mark-checkpoint-broken",
                ledger_revision=4,
                checkpoint_id=prepared["checkpoint_id"],
                expected_checkpoint_revision=2,
                broken_identity=commit_id,
                reason="history rewritten",
            )
        )
        self.assertEqual(returncode, 0, stderr)
        self.assertTrue(broken["original_fact_preserved"])
        returncode, ack_required, _ = self.run_cli(
            self.checkpoint_request(
                topic,
                operation="repair-checkpoint",
                ledger_revision=5,
                checkpoint_id=prepared["checkpoint_id"],
                expected_checkpoint_revision=3,
                replacement_commit=replacement_commit,
                replacement_base_ref=rewritten_parent,
                active_source_ack=None,
            )
        )
        self.assertEqual(returncode, 1)
        self.assertEqual(
            ack_required["error"]["code"],
            "checkpoint_active_source_ack_required",
        )
        returncode, injected, _ = self.run_cli(
            self.checkpoint_request(
                topic,
                operation="repair-checkpoint",
                ledger_revision=5,
                checkpoint_id=prepared["checkpoint_id"],
                expected_checkpoint_revision=3,
                replacement_commit=replacement_commit,
                replacement_base_ref=rewritten_parent,
                active_source_ack={
                    "acknowledged": True,
                    "checkpoint_id": prepared["checkpoint_id"],
                    "broken_identity": commit_id,
                },
            ),
            failpoint="repair-before-result-record",
        )
        self.assertEqual(returncode, 1)
        self.assertEqual(injected["error"]["code"], "injected_failure")
        returncode, repaired, stderr = self.run_cli(
            self.checkpoint_request(
                topic,
                operation="repair-checkpoint",
                ledger_revision=5,
                checkpoint_id=prepared["checkpoint_id"],
                expected_checkpoint_revision=3,
                replacement_commit=replacement_commit,
                replacement_base_ref=rewritten_parent,
                active_source_ack={
                    "acknowledged": True,
                    "checkpoint_id": prepared["checkpoint_id"],
                    "broken_identity": commit_id,
                },
            )
        )
        self.assertEqual(returncode, 0, stderr)
        self.assertEqual(repaired["broken_identity"], commit_id)
        self.assertEqual(repaired["replacement_identity"], replacement_commit)
        self.assertNotEqual(repaired["replacement_identity"], commit_id)
        self.assertTrue(repaired["original_fact_preserved"])
        returncode, validated, stderr = self.run_cli(
            self.checkpoint_request(topic, operation="validate")
        )
        self.assertEqual(returncode, 0, stderr)
        self.assertEqual(validated["checkpoint_count"], 1)

    def test_outcome_unknown_adopts_one_unreferenced_matching_commit_and_rejects_two(
        self,
    ) -> None:
        for match_count in (1, 2):
            with self.subTest(match_count=match_count):
                project = self.make_project(f"unknown-{match_count}", git=True)
                (project / "base.txt").write_text("base\n", encoding="utf-8")
                subprocess.run(
                    ["git", "-C", str(project), "add", "base.txt"], check=True
                )
                subprocess.run(
                    [
                        "git", "-C", str(project), "-c", "user.name=Test", "-c",
                        "user.email=test@example.com", "commit", "-qm", "base",
                    ],
                    check=True,
                )
                topic = self.bootstrap_topic(project)
                prepared = self.prepare_checkpoint(topic, ledger_revision=1)
                commits = [
                    self.create_matching_checkpoint_commit(
                        project,
                        prepared,
                        timestamp=f"2026-01-0{index + 1}T00:00:00+00:00",
                    )
                    for index in range(match_count)
                ]
                self.assertEqual(len(set(commits)), match_count)
                returncode, unknown, stderr = self.run_cli(
                    self.checkpoint_request(
                        topic,
                        operation="record-checkpoint-outcome-unknown",
                        ledger_revision=2,
                        checkpoint_id=prepared["checkpoint_id"],
                        expected_checkpoint_revision=1,
                    )
                )
                self.assertEqual(returncode, 0, stderr)
                returncode, reconciled, reconcile_stderr = self.run_cli(
                    self.checkpoint_request(
                        topic,
                        operation="reconcile-git-checkpoint",
                        ledger_revision=3,
                        checkpoint_id=prepared["checkpoint_id"],
                        expected_checkpoint_revision=unknown[
                            "checkpoint_record_revision"
                        ],
                    )
                )
                if match_count == 1:
                    self.assertEqual(returncode, 0, reconcile_stderr)
                    self.assertEqual(reconciled["state"], "completed")
                    self.assertEqual(reconciled["commit_id"], commits[0])
                    self.assertEqual(
                        subprocess.run(
                            [
                                "git", "-C", str(project), "rev-parse",
                                str(reconciled["checkpoint_ref"]),
                            ],
                            check=True,
                            stdout=subprocess.PIPE,
                            text=True,
                        ).stdout.strip(),
                        commits[0],
                    )
                else:
                    self.assertEqual(returncode, 1)
                    self.assertEqual(
                        reconciled["error"]["code"],
                        "checkpoint_history_ambiguous",
                    )

    def test_failure_injection_recovers_git_and_snapshot_result_recording(self) -> None:
        git_project = self.make_project("git-failure-injection", git=True)
        (git_project / "base.txt").write_text("base\n", encoding="utf-8")
        subprocess.run(["git", "-C", str(git_project), "add", "base.txt"], check=True)
        subprocess.run(
            [
                "git", "-C", str(git_project), "-c", "user.name=Test", "-c",
                "user.email=test@example.com", "commit", "-qm", "base",
            ],
            check=True,
        )
        topic = self.bootstrap_topic(git_project)
        failed_prepare = self.checkpoint_request(
            topic,
            operation="prepare-checkpoint",
            ledger_revision=1,
            purpose="pause",
            base_ref="HEAD",
        )
        returncode, injected, _ = self.run_cli(
            failed_prepare, failpoint="checkpoint-before-prepared-ledger-write"
        )
        self.assertEqual(returncode, 1)
        self.assertEqual(injected["error"]["code"], "injected_failure")
        returncode, validated, stderr = self.run_cli(
            self.checkpoint_request(topic, operation="validate")
        )
        self.assertEqual(returncode, 0, stderr)
        self.assertEqual(validated["ledger_revision"], 1)

        after_write_request = self.checkpoint_request(
            topic,
            operation="prepare-checkpoint",
            ledger_revision=1,
            purpose="pause",
            base_ref="HEAD",
        )
        returncode, injected, _ = self.run_cli(
            after_write_request, failpoint="checkpoint-after-prepared-ledger-write"
        )
        self.assertEqual(returncode, 1)
        self.assertEqual(injected["error"]["code"], "injected_failure")
        returncode, cancelled, stderr = self.run_cli(
            self.checkpoint_request(
                topic,
                operation="cancel-checkpoint",
                ledger_revision=2,
                checkpoint_id=f"CP-{uuid.UUID(str(after_write_request['idempotency_key'])).hex}",
                reason="recover injected prepared write",
            )
        )
        self.assertEqual(returncode, 0, stderr)

        prepared = self.prepare_checkpoint(topic, ledger_revision=3)
        lease = self.acquire_repository_coordination_lease(git_project)
        holder = lease["holder"]
        assert isinstance(holder, dict)
        publish_request = self.checkpoint_request(
            topic,
            operation="publish-git-checkpoint",
            ledger_revision=4,
            checkpoint_id=prepared["checkpoint_id"],
            expected_checkpoint_revision=1,
            repository_coordination_lease={
                "path": lease["path"],
                "lease_id": holder["lease_id"],
                "version": lease["version"],
            },
        )
        returncode, injected, _ = self.run_cli(
            publish_request, failpoint="git-before-commit-create"
        )
        self.assertEqual(returncode, 1)
        self.assertEqual(injected["error"]["code"], "injected_failure")
        returncode, injected, _ = self.run_cli(
            publish_request, failpoint="git-after-commit-before-result-record"
        )
        self.assertEqual(returncode, 1)
        returncode, unknown, stderr = self.run_cli(
            self.checkpoint_request(
                topic,
                operation="record-checkpoint-outcome-unknown",
                ledger_revision=4,
                checkpoint_id=prepared["checkpoint_id"],
                expected_checkpoint_revision=1,
            )
        )
        self.assertEqual(returncode, 0, stderr)
        returncode, adopted, stderr = self.run_cli(
            self.checkpoint_request(
                topic,
                operation="reconcile-git-checkpoint",
                ledger_revision=5,
                checkpoint_id=prepared["checkpoint_id"],
                expected_checkpoint_revision=unknown["checkpoint_record_revision"],
            )
        )
        self.assertEqual(returncode, 0, stderr)
        self.assertEqual(adopted["state"], "completed")

        non_git = self.make_project("snapshot-failure-injection", git=False)
        snapshot_topic = self.bootstrap_topic(non_git)
        snapshot_prepared = self.prepare_checkpoint(
            snapshot_topic, ledger_revision=1, base_ref="project-root"
        )
        snapshot_request = self.checkpoint_request(
            snapshot_topic,
            operation="publish-non-git-checkpoint",
            ledger_revision=2,
            checkpoint_id=snapshot_prepared["checkpoint_id"],
            expected_checkpoint_revision=1,
        )
        returncode, injected, _ = self.run_cli(
            snapshot_request, failpoint="snapshot-before-create"
        )
        self.assertEqual(returncode, 1)
        returncode, injected, _ = self.run_cli(
            snapshot_request, failpoint="snapshot-after-create-before-result-record"
        )
        self.assertEqual(returncode, 1)
        returncode, snapshot_unknown, stderr = self.run_cli(
            self.checkpoint_request(
                snapshot_topic,
                operation="record-checkpoint-outcome-unknown",
                ledger_revision=2,
                checkpoint_id=snapshot_prepared["checkpoint_id"],
                expected_checkpoint_revision=1,
            )
        )
        self.assertEqual(returncode, 0, stderr)
        Path(str(snapshot_topic["topic_document_path"])).write_text(
            "changed after uncertain snapshot\n", encoding="utf-8"
        )
        returncode, dry_run, stderr = self.run_cli(
            self.checkpoint_request(snapshot_topic, operation="checkpoint-gc-dry-run")
        )
        self.assertEqual(returncode, 0, stderr)
        self.assertEqual(dry_run["candidates"], [])
        returncode, reconciled, stderr = self.run_cli(
            self.checkpoint_request(
                snapshot_topic,
                operation="reconcile-non-git-checkpoint",
                ledger_revision=3,
                checkpoint_id=snapshot_prepared["checkpoint_id"],
                expected_checkpoint_revision=snapshot_unknown[
                    "checkpoint_record_revision"
                ],
            )
        )
        self.assertEqual(returncode, 0, stderr)
        self.assertEqual(reconciled["state"], "completed")

    def test_checkpoint_creation_identity_survives_recent_event_truncation(self) -> None:
        project = self.make_project("checkpoint-identity-window", git=False)
        topic = self.bootstrap_topic(project)
        creation_key = str(uuid.uuid4())
        request = self.checkpoint_request(
            topic,
            operation="prepare-checkpoint",
            ledger_revision=1,
            purpose="pause",
            base_ref="project-root",
            idempotency_key=creation_key,
        )
        returncode, prepared, stderr = self.run_cli(request)
        self.assertEqual(returncode, 0, stderr)
        returncode, cancelled, stderr = self.run_cli(
            self.checkpoint_request(
                topic,
                operation="cancel-checkpoint",
                ledger_revision=2,
                checkpoint_id=prepared["checkpoint_id"],
                reason="make room for real CLI event window",
            )
        )
        self.assertEqual(returncode, 0, stderr)
        ledger_revision = 3
        for _ in range(101):
            transient = self.prepare_checkpoint(
                topic, ledger_revision=ledger_revision, base_ref="project-root"
            )
            ledger_revision += 1
            returncode, _, stderr = self.run_cli(
                self.checkpoint_request(
                    topic,
                    operation="cancel-checkpoint",
                    ledger_revision=ledger_revision,
                    checkpoint_id=transient["checkpoint_id"],
                    reason="populate real recent-event window",
                )
            )
            self.assertEqual(returncode, 0, stderr)
            ledger_revision += 1
        self.assertEqual(ledger_revision, 205)
        returncode, replayed, stderr = self.run_cli(request)
        self.assertEqual(returncode, 0, stderr)
        self.assertTrue(replayed["idempotent_replay"])
        self.assertEqual(replayed["checkpoint_id"], prepared["checkpoint_id"])
        self.assertEqual(replayed["ledger_revision"], prepared["ledger_revision"])
        self.assertFalse(replayed["identity_reusable"])

        conflicting = dict(request)
        conflicting["purpose"] = "handoff"
        returncode, conflict, _ = self.run_cli(conflicting)
        self.assertEqual(returncode, 1)
        self.assertEqual(conflict["error"]["code"], "idempotency_conflict")

        returncode, inspected, stderr = self.run_cli(
            self.checkpoint_request(topic, operation="read-topic")
        )
        self.assertEqual(returncode, 0, stderr)
        matches = [
            item
            for item in inspected["checkpoints"]
            if item["checkpoint_id"] == prepared["checkpoint_id"]
        ]
        self.assertEqual(len(matches), 1)

    def test_gc_outcome_unknown_recovers_mid_delete_without_touching_workspace(self) -> None:
        project = self.make_project("gc-mid-delete", git=False)
        workspace_file = project / "unrelated.txt"
        workspace_file.write_text("preserve me\n", encoding="utf-8")
        topic = self.bootstrap_topic(project)
        snapshot_root = Path(str(topic["ledger_path"])).parents[4] / "checkpoints" / "sha256"
        candidates = []
        for content in (b'{"orphan":1}\n', b'{"orphan":2}\n'):
            digest = hashlib.sha256(content).hexdigest()
            path = snapshot_root / digest[:2] / digest
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(content)
            candidates.append({"digest": digest, "path": str(path)})
        returncode, dry_run, stderr = self.run_cli(
            self.checkpoint_request(topic, operation="checkpoint-gc-dry-run")
        )
        self.assertEqual(returncode, 0, stderr)
        self.assertEqual(dry_run["candidates"], sorted(candidates, key=lambda item: item["path"]))
        confirm_key = str(uuid.uuid4())
        confirm_request = self.checkpoint_request(
            topic,
            operation="checkpoint-gc-confirm",
            ledger_revision=1,
            candidate_digest=dry_run["candidate_digest"],
            candidates=dry_run["candidates"],
            idempotency_key=confirm_key,
        )
        returncode, injected, _ = self.run_cli(
            confirm_request, failpoint="gc-during-delete"
        )
        self.assertEqual(returncode, 1)
        self.assertEqual(injected["error"]["code"], "injected_failure")
        self.assertEqual(workspace_file.read_text(encoding="utf-8"), "preserve me\n")
        remaining = [Path(str(item["path"])).exists() for item in dry_run["candidates"]]
        self.assertEqual(remaining.count(False), 1)
        self.assertEqual(remaining.count(True), 1)

        returncode, blocked, _ = self.run_cli(
            self.checkpoint_request(topic, operation="checkpoint-gc-dry-run")
        )
        self.assertEqual(returncode, 1)
        self.assertEqual(
            blocked["error"]["code"], "checkpoint_gc_reconciliation_required"
        )
        gc_operation_id = f"GC-{uuid.UUID(confirm_key).hex}"
        returncode, recovered, stderr = self.run_cli(
            self.checkpoint_request(
                topic,
                operation="reconcile-checkpoint-gc",
                ledger_revision=2,
                gc_operation_id=gc_operation_id,
                expected_gc_revision=1,
            )
        )
        self.assertEqual(returncode, 0, stderr)
        self.assertTrue(recovered["recovered_outcome_unknown"])
        self.assertEqual(len(recovered["already_deleted"]), 1)
        self.assertEqual(len(recovered["deleted_during_reconciliation"]), 1)
        self.assertTrue(all(not Path(str(item["path"])).exists() for item in candidates))
        self.assertEqual(workspace_file.read_text(encoding="utf-8"), "preserve me\n")

    def test_gc_outcome_unknown_recovers_after_all_deletes_before_result_record(self) -> None:
        project = self.make_project("gc-after-delete", git=False)
        workspace_file = project / "unrelated.txt"
        workspace_file.write_text("preserve me\n", encoding="utf-8")
        topic = self.bootstrap_topic(project)
        snapshot_root = Path(str(topic["ledger_path"])).parents[4] / "checkpoints" / "sha256"
        content = b'{"orphan":true}\n'
        digest = hashlib.sha256(content).hexdigest()
        path = snapshot_root / digest[:2] / digest
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content)
        returncode, dry_run, stderr = self.run_cli(
            self.checkpoint_request(topic, operation="checkpoint-gc-dry-run")
        )
        self.assertEqual(returncode, 0, stderr)
        confirm_key = str(uuid.uuid4())
        returncode, injected, _ = self.run_cli(
            self.checkpoint_request(
                topic,
                operation="checkpoint-gc-confirm",
                ledger_revision=1,
                candidate_digest=dry_run["candidate_digest"],
                candidates=dry_run["candidates"],
                idempotency_key=confirm_key,
            ),
            failpoint="gc-after-delete-before-result-record",
        )
        self.assertEqual(returncode, 1)
        self.assertFalse(path.exists())
        returncode, recovered, stderr = self.run_cli(
            self.checkpoint_request(
                topic,
                operation="reconcile-checkpoint-gc",
                ledger_revision=2,
                gc_operation_id=f"GC-{uuid.UUID(confirm_key).hex}",
                expected_gc_revision=1,
            )
        )
        self.assertEqual(returncode, 0, stderr)
        self.assertEqual(recovered["already_deleted"], dry_run["candidates"])
        self.assertEqual(recovered["deleted_during_reconciliation"], [])
        self.assertEqual(workspace_file.read_text(encoding="utf-8"), "preserve me\n")

    def test_gc_reconciliation_rejects_tampered_external_candidate_path(self) -> None:
        project = self.make_project("gc-path-confinement", git=False)
        topic = self.bootstrap_topic(project)
        snapshot_root = Path(str(topic["ledger_path"])).parents[4] / "checkpoints" / "sha256"
        content = b'{"orphan":true}\n'
        digest = hashlib.sha256(content).hexdigest()
        canonical_path = snapshot_root / digest[:2] / digest
        canonical_path.parent.mkdir(parents=True, exist_ok=True)
        canonical_path.write_bytes(content)
        returncode, dry_run, stderr = self.run_cli(
            self.checkpoint_request(topic, operation="checkpoint-gc-dry-run")
        )
        self.assertEqual(returncode, 0, stderr)
        confirm_key = str(uuid.uuid4())
        returncode, _, stderr = self.run_cli(
            self.checkpoint_request(
                topic,
                operation="checkpoint-gc-confirm",
                ledger_revision=1,
                candidate_digest=dry_run["candidate_digest"],
                candidates=dry_run["candidates"],
                idempotency_key=confirm_key,
            ),
            failpoint="gc-after-outcome-unknown-record",
        )
        self.assertEqual(returncode, 1, stderr)
        ledger_path = Path(str(topic["ledger_path"]))
        ledger_text = ledger_path.read_text(encoding="utf-8")
        external = project / "unrelated.txt"
        external.write_bytes(content)
        ledger_text = ledger_text.replace(str(canonical_path), str(external))
        frontmatter, body = ledger_text[4:].split("\n---\n", 1)
        without_digest = "\n".join(
            line for line in frontmatter.splitlines() if not line.startswith("content_digest: ")
        ) + "\n"
        tampered_digest = hashlib.sha256((without_digest + body).encode("utf-8")).hexdigest()
        ledger_path.write_text(
            ledger_text.replace(
                re.search(r"content_digest: [0-9a-f]{64}", ledger_text).group(0),
                f"content_digest: {tampered_digest}",
            ),
            encoding="utf-8",
        )
        returncode, rejected, _ = self.run_cli(
            self.checkpoint_request(
                topic,
                operation="reconcile-checkpoint-gc",
                ledger_revision=2,
                gc_operation_id=f"GC-{uuid.UUID(confirm_key).hex}",
                expected_gc_revision=1,
            )
        )
        self.assertEqual(returncode, 1)
        self.assertIn(rejected["error"]["code"], {"checkpoint_snapshot_corrupt", "state_corrupt"})
        self.assertTrue(external.exists())
        self.assertTrue(canonical_path.exists())

    def test_non_git_snapshot_is_immutable_reusable_and_gc_confirmation_is_exact(
        self,
    ) -> None:
        project = self.make_project("snapshot-checkpoint", git=False)
        topic = self.bootstrap_topic(project)
        first = self.prepare_checkpoint(topic, ledger_revision=1, base_ref="project-root")
        returncode, published, stderr = self.run_cli(
            self.checkpoint_request(
                topic,
                operation="publish-non-git-checkpoint",
                ledger_revision=2,
                checkpoint_id=first["checkpoint_id"],
                expected_checkpoint_revision=1,
            )
        )
        self.assertEqual(returncode, 0, stderr)
        snapshot_path = Path(str(published["snapshot_path"]))
        self.assertEqual(
            hashlib.sha256(snapshot_path.read_bytes()).hexdigest(),
            published["snapshot_digest"],
        )
        self.assertEqual(snapshot_path.stat().st_mode & 0o777, 0o400)
        second = self.prepare_checkpoint(topic, ledger_revision=3, base_ref="project-root")
        returncode, reused, stderr = self.run_cli(
            self.checkpoint_request(
                topic,
                operation="publish-non-git-checkpoint",
                ledger_revision=4,
                checkpoint_id=second["checkpoint_id"],
                expected_checkpoint_revision=1,
            )
        )
        self.assertEqual(returncode, 0, stderr)
        self.assertTrue(reused["snapshot_reused"])
        self.assertEqual(reused["snapshot_digest"], published["snapshot_digest"])
        orphan_bytes = b'{"orphan":true}\n'
        orphan_digest = hashlib.sha256(orphan_bytes).hexdigest()
        orphan_path = snapshot_path.parents[1] / orphan_digest[:2] / orphan_digest
        orphan_path.parent.mkdir(parents=True, exist_ok=True)
        orphan_path.write_bytes(orphan_bytes)
        returncode, dry_run, stderr = self.run_cli(
            self.checkpoint_request(topic, operation="checkpoint-gc-dry-run")
        )
        self.assertEqual(returncode, 0, stderr)
        self.assertEqual(dry_run["candidates"], [{"digest": orphan_digest, "path": str(orphan_path)}])
        returncode, mismatch, _ = self.run_cli(
            self.checkpoint_request(
                topic,
                operation="checkpoint-gc-confirm",
                ledger_revision=5,
                candidate_digest="0" * 64,
                candidates=dry_run["candidates"],
            )
        )
        self.assertEqual(returncode, 1)
        self.assertEqual(
            mismatch["error"]["code"], "checkpoint_gc_confirmation_mismatch"
        )
        self.assertTrue(orphan_path.exists())
        returncode, injected, _ = self.run_cli(
            self.checkpoint_request(
                topic,
                operation="checkpoint-gc-confirm",
                ledger_revision=5,
                candidate_digest=dry_run["candidate_digest"],
                candidates=dry_run["candidates"],
            ),
            failpoint="gc-before-delete",
        )
        self.assertEqual(returncode, 1)
        self.assertEqual(injected["error"]["code"], "injected_failure")
        self.assertTrue(orphan_path.exists())
        returncode, deleted, stderr = self.run_cli(
            self.checkpoint_request(
                topic,
                operation="checkpoint-gc-confirm",
                ledger_revision=5,
                candidate_digest=dry_run["candidate_digest"],
                candidates=dry_run["candidates"],
            )
        )
        self.assertEqual(returncode, 0, stderr)
        self.assertEqual(deleted["deleted"], dry_run["candidates"])
        self.assertFalse(orphan_path.exists())
        self.assertTrue(snapshot_path.exists())


if __name__ == "__main__":
    unittest.main()
