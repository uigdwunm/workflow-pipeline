#!/usr/bin/env python3

from __future__ import annotations

import json
import hashlib
from pathlib import Path
import re
import subprocess
import sys
import tempfile
import unittest
import uuid


SCRIPT_PATH = Path(__file__).with_name("discussion_protocol.py")


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

    def run_cli(self, request: dict[str, object]) -> tuple[int, dict[str, object], str]:
        completed = subprocess.run(
            [sys.executable, str(SCRIPT_PATH)],
            input=json.dumps(request),
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
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


if __name__ == "__main__":
    unittest.main()
