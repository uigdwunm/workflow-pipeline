#!/usr/bin/env python3

from __future__ import annotations

import json
import hashlib
import fcntl
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile
import time
import unittest
import uuid
from concurrent.futures import ThreadPoolExecutor
from unittest import mock

sys.path.insert(0, str(Path(__file__).parent))
import discussion_protocol as PROTOCOL
from discussion_core import RequestContext


SCRIPT_PATH = Path(__file__).with_name("discussion_protocol.py")


class DiscussionProtocolTestSupport(unittest.TestCase):
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
        environment_overrides: dict[str, str] | None = None,
    ) -> tuple[int, dict[str, object], str]:
        environment = dict(os.environ)
        if environment_overrides is not None:
            environment.update(environment_overrides)
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

    def run_raw_cli(self, request: bytes) -> tuple[int, dict[str, object], str]:
        completed = subprocess.run(
            [sys.executable, str(SCRIPT_PATH)],
            input=request,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        self.assertTrue(completed.stdout, completed.stderr.decode())
        return (
            completed.returncode,
            json.loads(completed.stdout),
            completed.stderr.decode(),
        )

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

    def replace_recent_events_with_retained_window(
        self, ledger_path: Path, topic_id: str
    ) -> None:
        frontmatter, records = PROTOCOL._load_records(ledger_path)
        records["Recent Events"] = [
            {
                "event_id": f"event-{revision:08d}",
                "event_type": "retained-test-event",
                "ledger_revision": revision,
                "topic_id": topic_id,
                "idempotency_key": str(uuid.UUID(int=revision, version=4)),
                "request_fingerprint": hashlib.sha256(
                    f"retained-test-event:{revision}".encode("utf-8")
                ).hexdigest(),
                "result_json": "{}",
            }
            for revision in range(2, 202)
        ]
        frontmatter["ledger_revision"] = "201"
        frontmatter["event_count"] = "201"
        ledger_path.write_bytes(PROTOCOL._render_records_ledger(frontmatter, records))

    def downgrade_ledger_to_v1(
        self, ledger_path: Path, *, keep_creation_event: bool
    ) -> None:
        frontmatter, records = PROTOCOL._load_records(ledger_path)
        frontmatter["schema_version"] = "1"
        frontmatter.pop("creation_idempotency_key", None)
        frontmatter.pop("creation_fingerprint", None)
        if not keep_creation_event:
            records["Recent Events"] = [
                event
                for event in records["Recent Events"]
                if event.get("event_type") != "root-topic-bootstrapped"
            ]
        ledger_path.write_bytes(PROTOCOL._render_records_ledger(frontmatter, records))

    def replace_empty_ledger_section(
        self, ledger_path: Path, section: str, records: list[dict[str, object]]
    ) -> None:
        lines = ["```yaml", "records:"]
        for record in records:
            for index, (key, value) in enumerate(record.items()):
                prefix = "  - " if index == 0 else "    "
                scalar = (
                    "true" if value is True else
                    "false" if value is False else
                    "null" if value is None else
                    str(value) if isinstance(value, int) else
                    json.dumps(value, ensure_ascii=False)
                )
                lines.append(f"{prefix}{key}: {scalar}")
        lines.append("```")
        old = f"## {section}\n\n```yaml\nrecords:\n  []\n```"
        new = f"## {section}\n\n" + "\n".join(lines)
        self.rewrite_ledger_with_valid_digest(ledger_path, old, new)

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
        self.assertIn("schema_version: 3", ledger_text)
        self.assertRegex(
            ledger_text,
            r"creation_idempotency_key: \"[0-9a-f-]{36}\"",
        )
        self.assertRegex(
            ledger_text,
            r"creation_fingerprint: \"[0-9a-f]{64}\"",
        )
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


class DiscussionProtocolBootstrapTests(DiscussionProtocolTestSupport):
    def test_operation_registry_contains_only_current_operation_names(self) -> None:
        names = PROTOCOL.OPERATION_REGISTRY.names
        self.assertEqual(
            names,
            (
                "bootstrap",
                "discover-context",
                "initialize-document-context",
                "prepare-phase-run",
                "prepare-wrapper-phase-run",
                "prepare-no-code-integration-run",
                "authorize-continuous-flow",
                "phase-ready",
                "authorize-phase-carrier",
                "claim-phase-carrier",
                "phase-activate",
                "revoke-phase-authorization",
                "supersede-phase-run",
                "claim-phase-completion",
                "complete-phase-run",
                "finalize-phase-run",
                "cancel-phase-run",
                "fail-phase-run",
                "phase-outcome-unknown",
                "retry-phase-run",
                "reconcile-phase-run",
                "read-phase-run",
                "reopen-phase",
                "prepare-topic-update",
                "apply-document-write",
                "prepare-checkpoint",
                "cancel-checkpoint",
                "publish-git-checkpoint",
                "publish-non-git-checkpoint",
                "record-checkpoint-outcome-unknown",
                "reconcile-git-checkpoint",
                "reconcile-non-git-checkpoint",
                "mark-checkpoint-broken",
                "repair-checkpoint",
                "checkpoint-gc-dry-run",
                "checkpoint-gc-confirm",
                "reconcile-checkpoint-gc",
                "prepare-handoff",
                "update-topic-dependency",
                "evaluate-topic-gate",
                "release-topic-gate",
                "bind-handoff",
                "accept-handoff",
                "authorize-handoff-discussion",
                "record-handoff-outcome-unknown",
                "record-handoff-failure",
                "cancel-handoff-attempt",
                "retry-handoff",
                "reconcile-handoff-attempt",
                "submit-child-result",
                "record-child-result",
                "read-handoff",
                "read-topic",
            ),
        )

    def test_removed_operation_names_are_rejected(self) -> None:
        for operation in ("block-phase-run", "validate"):
            with self.subTest(operation=operation):
                code, response, _ = self.run_cli(
                    {"protocol_version": 1, "operation": operation}
                )
                self.assertEqual(code, 1)
                self.assertEqual(response["error"]["code"], "unsupported_operation")

    def test_request_context_lazily_parses_each_common_field_once(self) -> None:
        calls = {"project": 0, "string": 0}

        def project(value: object) -> Path:
            calls["project"] += 1
            return Path(str(value))

        def string(value: object, label: str, *, max_bytes: int = 512) -> str:
            calls["string"] += 1
            return str(value)

        context = RequestContext.parse(
            {
                "protocol_version": 1,
                "operation": "read-topic",
                "project_path": "/tmp/project",
                "project_id": "project-" + "1" * 32,
            },
            protocol_version=1,
            error_type=PROTOCOL.ProtocolError,
            parse_project_path=project,
            parse_string=string,
            known_operations=PROTOCOL.OPERATION_REGISTRY.names,
        )
        self.assertEqual(context.project_path, context.project_path)
        self.assertEqual(context.project_id, context.project_id)
        self.assertEqual(calls, {"project": 1, "string": 1})

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

    def test_linked_worktree_uses_the_shared_git_coordination_storage(self) -> None:
        repository = self.make_project("linked-source", git=True)
        subprocess.run(
            ["git", "-C", str(repository), "-c", "user.name=Test", "-c", "user.email=test@example.com", "commit", "--allow-empty", "-qm", "base"],
            check=True,
        )
        linked = self.root / "linked-checkout"
        subprocess.run(
            ["git", "-C", str(repository), "worktree", "add", "-q", "-b", "linked-test", str(linked)],
            check=True,
        )

        returncode, response, stderr = self.run_cli(self.request(linked))

        self.assertEqual(returncode, 0, stderr)
        self.assertTrue(
            Path(str(response["ledger_path"])).is_relative_to(
                self.git_common_dir(repository) / "cc-switch" / "design-discussion" / "v1"
            )
        )

    def test_git_runtime_failure_fails_closed_without_project_local_state(self) -> None:
        project = self.make_project("git-probe-failure", git=False)
        fake_bin = self.root / "fake-bin"
        fake_bin.mkdir()
        fake_git = fake_bin / "git"
        fake_git.write_text("#!/bin/sh\necho 'simulated git permission failure' >&2\nexit 2\n", encoding="utf-8")
        fake_git.chmod(0o755)

        returncode, response, _ = self.run_cli(
            self.request(project), environment_overrides={"PATH": str(fake_bin)}
        )

        self.assertEqual(returncode, 1)
        self.assertEqual(response["error"]["code"], "git_probe_failed")
        self.assertFalse((project / ".codex").exists())

    def test_git_probe_exceptions_are_typed_fail_closed_errors(self) -> None:
        project = self.make_project("missing-git", git=False)
        with mock.patch.object(PROTOCOL.subprocess, "run", side_effect=FileNotFoundError("private path")):
            with self.assertRaises(PROTOCOL.ProtocolError) as raised:
                PROTOCOL._git_common_dir(project)
        self.assertEqual(raised.exception.code, "git_probe_failed")

    def test_git_probe_rejects_an_empty_or_relative_common_directory(self) -> None:
        project = self.make_project("invalid-common-dir", git=False)
        membership = subprocess.CompletedProcess([], 0, "true\n", "")
        for common_output in ("", "relative-git-dir\n"):
            with self.subTest(common_output=common_output):
                common = subprocess.CompletedProcess([], 0, common_output, "")
                with mock.patch.object(PROTOCOL.subprocess, "run", side_effect=[membership, common]):
                    with self.assertRaises(PROTOCOL.ProtocolError) as raised:
                        PROTOCOL._git_common_dir(project)
                self.assertEqual(raised.exception.code, "git_identity_invalid")

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
        lock_path = coordination_root / "locks" / PROTOCOL._project_lock_name(project)
        self.assertTrue(lock_path.is_file())

    def test_bootstrap_lock_wait_is_bounded_and_returns_coordination_busy(self) -> None:
        project = self.make_project("bootstrap-lock-timeout", git=True)
        coordination_root = self.git_common_dir(project) / "cc-switch" / "design-discussion" / "v1"
        lock_path = coordination_root / "locks" / PROTOCOL._project_lock_name(project)
        lock_path.parent.mkdir(parents=True)
        with lock_path.open("a+b") as lock_stream:
            fcntl.flock(lock_stream.fileno(), fcntl.LOCK_EX)
            started = time.monotonic()
            returncode, response, _ = self.run_cli(
                self.request(project),
                environment_overrides={"CODEX_DISCUSSION_TEST_LOCK_TIMEOUT_SECONDS": "0.2"},
            )
            elapsed = time.monotonic() - started
        self.assertEqual(returncode, 1)
        self.assertEqual(response["error"]["code"], "coordination_busy")
        self.assertLess(elapsed, 2.0)
        self.assertTrue(lock_path.is_file())

    def test_failed_bootstrap_never_replaces_the_lock_inode_seen_by_waiters(self) -> None:
        project = self.make_project("stable-bootstrap-lock", git=True)
        blocker = project / "docs" / "discussions" / "checkout-redesign" / "topic.md"
        blocker.parent.mkdir(parents=True)
        blocker.write_text("block bootstrap\n", encoding="utf-8")
        coordination_root = self.git_common_dir(project) / "cc-switch" / "design-discussion" / "v1"
        lock_path = coordination_root / "locks" / PROTOCOL._project_lock_name(project)

        first_code, _, _ = self.run_cli(self.request(project))
        self.assertEqual(first_code, 1)
        first_inode = lock_path.stat().st_ino
        with ThreadPoolExecutor(max_workers=2) as executor:
            outcomes = list(executor.map(lambda _: self.run_cli(self.request(project))[0], range(2)))
        self.assertEqual(outcomes, [1, 1])
        self.assertEqual(lock_path.stat().st_ino, first_inode)

    def test_cli_returns_one_structured_json_for_non_utf8_input(self) -> None:
        invalid = subprocess.run(
            [sys.executable, str(SCRIPT_PATH)],
            input=b"\xff",
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        self.assertEqual(invalid.returncode, 1)
        invalid_lines = invalid.stdout.splitlines()
        self.assertEqual(len(invalid_lines), 1)
        self.assertEqual(json.loads(invalid_lines[0])["error"]["code"], "invalid_json")

    def test_cli_rejects_duplicate_json_keys_without_writing_project_state(self) -> None:
        project = self.make_project("duplicate-request-key", git=False)
        request = json.dumps(
            self.request(project),
            ensure_ascii=False,
            separators=(",", ":"),
        )
        request = request.replace(
            '"root_slug":"checkout-redesign"',
            '"root_slug":"ignored-topic","root_slug":"checkout-redesign"',
        )
        self.assertEqual(request.count('"root_slug"'), 2)

        code, response, _ = self.run_raw_cli(request.encode("utf-8"))

        self.assertEqual(code, 1)
        self.assertEqual(response["error"]["code"], "invalid_json")
        self.assertEqual(list(project.iterdir()), [])

    def test_cli_rejects_duplicate_keys_in_nested_json_objects(self) -> None:
        request = (
            b'{"protocol_version":1,"operation":"unsupported",'
            b'"nested":{"key":"first","key":"second"}}'
        )

        code, response, _ = self.run_raw_cli(request)

        self.assertEqual(code, 1)
        self.assertEqual(response["error"]["code"], "invalid_json")

    def test_cli_rejects_non_integer_json_numbers(self) -> None:
        for number in ("1.0", "NaN", "Infinity", "-Infinity"):
            with self.subTest(number=number):
                request = (
                    f'{{"protocol_version":{number},'
                    '"operation":"unsupported"}'
                ).encode("utf-8")

                code, response, _ = self.run_raw_cli(request)

                self.assertEqual(code, 1)
                self.assertEqual(response["error"]["code"], "invalid_json")

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


class DiscussionProtocolEvolutionTests(DiscussionProtocolTestSupport):
    def bootstrap_topic(
        self, project: Path, *, owner_ref: str = "discussion-task"
    ) -> tuple[dict[str, object], dict[str, str]]:
        returncode, response, stderr = self.run_cli(
            self.request(project, conversation_ref=owner_ref)
        )
        self.assertEqual(returncode, 0, stderr)
        return response

    def test_bootstrap_replay_reads_evolved_ledger_without_rewriting_it(self) -> None:
        project = self.make_project("bootstrap-replay-after-evolution", git=False)
        invocation_id = str(uuid.uuid4())
        request = self.request(
            project,
            invocation_id=invocation_id,
            conversation_ref="discussion-task",
        )
        code, topic, stderr = self.run_cli(request)
        self.assertEqual(code, 0, stderr)
        prepared = self.prepare_phase_run(topic)
        ledger_path = Path(str(topic["ledger_path"]))
        evolved_bytes = ledger_path.read_bytes()

        code, replayed, stderr = self.run_cli(request)

        self.assertEqual(code, 0, stderr)
        self.assertTrue(replayed["idempotent_replay"])
        self.assertEqual(replayed["ledger_revision"], prepared["ledger_revision"])
        self.assertEqual(ledger_path.read_bytes(), evolved_bytes)

    def test_bootstrap_replay_survives_recent_event_retention(self) -> None:
        project = self.make_project("bootstrap-replay-after-retention", git=False)
        invocation_id = str(uuid.uuid4())
        request = self.request(
            project,
            invocation_id=invocation_id,
            conversation_ref="discussion-task",
        )
        code, topic, stderr = self.run_cli(request)
        self.assertEqual(code, 0, stderr)
        ledger_path = Path(str(topic["ledger_path"]))
        self.replace_recent_events_with_retained_window(
            ledger_path, str(topic["topic_id"])
        )
        retained_bytes = ledger_path.read_bytes()

        code, replayed, stderr = self.run_cli(request)

        self.assertEqual(code, 0, stderr)
        self.assertTrue(replayed["idempotent_replay"])
        self.assertEqual(replayed["ledger_revision"], 201)
        self.assertEqual(ledger_path.read_bytes(), retained_bytes)

    def test_v1_creation_event_supports_read_only_replay_and_next_write_upgrade(self) -> None:
        project = self.make_project("v1-bootstrap-replay", git=False)
        invocation_id = str(uuid.uuid4())
        request = self.request(
            project,
            invocation_id=invocation_id,
            conversation_ref="discussion-task",
        )
        code, topic, stderr = self.run_cli(request)
        self.assertEqual(code, 0, stderr)
        ledger_path = Path(str(topic["ledger_path"]))
        self.downgrade_ledger_to_v1(ledger_path, keep_creation_event=True)
        v1_bytes = ledger_path.read_bytes()

        code, replayed, stderr = self.run_cli(request)
        self.assertEqual(code, 0, stderr)
        self.assertTrue(replayed["idempotent_replay"])
        self.assertEqual(ledger_path.read_bytes(), v1_bytes)

        prepared = self.prepare_phase_run(topic)
        upgraded = ledger_path.read_text(encoding="utf-8")
        self.assertEqual(prepared["ledger_revision"], 2)
        self.assertIn("schema_version: 3", upgraded)
        self.assertIn("creation_idempotency_key:", upgraded)
        self.assertIn("creation_fingerprint:", upgraded)

    def test_v1_without_creation_event_keeps_working_but_cannot_prove_replay(self) -> None:
        project = self.make_project("v1-bootstrap-without-receipt", git=False)
        invocation_id = str(uuid.uuid4())
        request = self.request(
            project,
            invocation_id=invocation_id,
            conversation_ref="discussion-task",
        )
        code, topic, stderr = self.run_cli(request)
        self.assertEqual(code, 0, stderr)
        ledger_path = Path(str(topic["ledger_path"]))
        self.downgrade_ledger_to_v1(ledger_path, keep_creation_event=False)

        code, replayed, _ = self.run_cli(request)
        self.assertEqual(code, 1)
        self.assertEqual(
            replayed["error"]["code"], "discussion_already_initialized"
        )

        code, current, stderr = self.run_cli(
            self.evolution_request(topic, operation="read-topic")
        )
        self.assertEqual(code, 0, stderr)
        self.assertEqual(current["current_phase"], 0)
        prepared = self.prepare_phase_run(topic)
        self.assertEqual(prepared["ledger_revision"], 2)
        self.assertIn(
            "schema_version: 3", ledger_path.read_text(encoding="utf-8")
        )

    def test_v1_special_topic_update_write_promotes_only_on_commit(self) -> None:
        project = self.make_project("v1-special-write-promotion", git=False)
        topic = self.bootstrap_topic(project)
        ledger_path = Path(str(topic["ledger_path"]))
        self.downgrade_ledger_to_v1(ledger_path, keep_creation_event=True)
        before_read = ledger_path.read_bytes()

        code, _, stderr = self.run_cli(
            self.evolution_request(topic, operation="read-topic")
        )

        self.assertEqual(code, 0, stderr)
        self.assertEqual(ledger_path.read_bytes(), before_read)
        code, prepared, stderr = self.run_cli(
            self.evolution_request(
                topic,
                operation="prepare-topic-update",
                expected_revision=1,
                expected_topic_revision=1,
                mutation={
                    "type": "confirm-decision",
                    "summary": "The special write upgrades the ledger.",
                    "rationale": "Promotion belongs to persistence.",
                },
            )
        )

        self.assertEqual(code, 0, stderr)
        self.assertEqual(prepared["ledger_revision"], 2)
        self.assertIn("schema_version: 3", ledger_path.read_text(encoding="utf-8"))

    def test_read_topic_returns_current_lifecycle_state(self) -> None:
        project = self.make_project("read-topic-lifecycle", git=False)
        topic = self.bootstrap_topic(project)

        code, current, stderr = self.run_cli(
            self.evolution_request(topic, operation="read-topic")
        )

        self.assertEqual(code, 0, stderr)
        self.assertEqual(current["current_phase"], 0)
        self.assertEqual(current["phase_state"], "active")
        self.assertEqual(current["review_state"], "unreviewed")
        self.assertEqual(current["topic_state"], "open")

    def test_read_topic_rejects_absolute_manifest_slug(self) -> None:
        project = self.make_project("read-topic-absolute-slug", git=False)
        topic = self.bootstrap_topic(project)
        topic_path = Path(str(topic["topic_document_path"]))
        external_root = self.root / "external-topic-root"
        external_root.mkdir()
        (external_root / "topic.md").write_bytes(topic_path.read_bytes())
        manifest_path = project / "docs" / "discussions" / ".codex-project.md"
        manifest_path.write_text(
            manifest_path.read_text(encoding="utf-8").replace(
                "root_slug: checkout-redesign",
                f"root_slug: {external_root}",
            ),
            encoding="utf-8",
        )

        returncode, rejected, _ = self.run_cli(
            self.evolution_request(topic, operation="read-topic")
        )

        self.assertEqual(returncode, 1)
        self.assertEqual(rejected["error"]["code"], "state_corrupt")

    def test_valid_manifest_slug_retarget_is_rejected_by_topic_consumers(self) -> None:
        for operation in ("read-topic", "prepare-checkpoint", "prepare-phase-run"):
            with self.subTest(operation=operation):
                project = self.make_project(f"valid-slug-retarget-{operation}", git=False)
                topic = self.bootstrap_topic(project)
                topic_path = Path(str(topic["topic_document_path"]))
                sibling_path = (
                    project / "docs" / "discussions" / "sibling-topic" / "topic.md"
                )
                sibling_path.parent.mkdir(parents=True)
                sibling_path.write_bytes(topic_path.read_bytes())
                manifest_path = project / "docs" / "discussions" / ".codex-project.md"
                manifest_path.write_text(
                    manifest_path.read_text(encoding="utf-8").replace(
                        "root_slug: checkout-redesign",
                        "root_slug: sibling-topic",
                    ),
                    encoding="utf-8",
                )
                if operation == "read-topic":
                    request = self.evolution_request(topic, operation=operation)
                elif operation == "prepare-checkpoint":
                    request = self.checkpoint_request(
                        topic,
                        operation=operation,
                        ledger_revision=1,
                        purpose="pause",
                        base_ref="project-root",
                    )
                else:
                    request = self.phase_request(
                        topic,
                        operation,
                        1,
                        from_phase=0,
                        to_phase=1,
                        route="0->1",
                        carrier_kind="worker",
                    )

                returncode, rejected, _ = self.run_cli(request)

                self.assertEqual(returncode, 1)
                self.assertEqual(rejected["error"]["code"], "state_corrupt")

    def test_cancel_checkpoint_rejects_manifest_slug_retarget_without_mutating_ledger(
        self,
    ) -> None:
        project = self.make_project("cancel-checkpoint-slug-retarget", git=False)
        topic = self.bootstrap_topic(project)
        prepared = self.prepare_checkpoint(
            topic,
            ledger_revision=1,
            base_ref="project-root",
        )
        topic_path = Path(str(topic["topic_document_path"]))
        sibling_path = project / "docs" / "discussions" / "sibling-topic" / "topic.md"
        sibling_path.parent.mkdir(parents=True)
        sibling_path.write_bytes(topic_path.read_bytes())
        manifest_path = project / "docs" / "discussions" / ".codex-project.md"
        manifest_path.write_text(
            manifest_path.read_text(encoding="utf-8").replace(
                "root_slug: checkout-redesign",
                "root_slug: sibling-topic",
            ),
            encoding="utf-8",
        )
        ledger_path = Path(str(topic["ledger_path"]))
        ledger_before = ledger_path.read_bytes()

        returncode, rejected, _ = self.run_cli(
            self.checkpoint_request(
                topic,
                operation="cancel-checkpoint",
                ledger_revision=2,
                checkpoint_id=prepared["checkpoint_id"],
                reason="manifest path drift",
            )
        )

        self.assertEqual(returncode, 1)
        self.assertEqual(rejected["error"]["code"], "state_corrupt")
        self.assertEqual(ledger_path.read_bytes(), ledger_before)

    def test_v2_ledger_requires_a_complete_creation_receipt(self) -> None:
        project = self.make_project("v2-creation-receipt", git=False)
        topic = self.bootstrap_topic(project)
        ledger_path = Path(str(topic["ledger_path"]))
        fingerprint_line = re.search(
            r'^creation_fingerprint: "[0-9a-f]{64}"\n',
            ledger_path.read_text(encoding="utf-8"),
            flags=re.MULTILINE,
        )
        self.assertIsNotNone(fingerprint_line)
        self.rewrite_ledger_with_valid_digest(
            ledger_path, fingerprint_line.group(0), ""
        )

        code, response, _ = self.run_cli(
            self.evolution_request(topic, operation="read-topic")
        )

        self.assertEqual(code, 1)
        self.assertEqual(response["error"]["code"], "state_corrupt")

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
        self,
        topic,
        *,
        revision=1,
        topic_revision=1,
        from_phase=0,
        to_phase=1,
        carrier_kind="worker",
    ):
        code, prepared, stderr = self.run_cli(
            self.phase_request(
                topic,
                "prepare-phase-run",
                revision,
                topic_revision=topic_revision,
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

    def complete_current_topic_phase(
        self,
        topic: dict[str, object],
        *,
        ledger_revision: int,
        topic_revision: int,
        from_phase: int,
        to_phase: int,
    ) -> tuple[dict[str, object], int, int]:
        prepared = self.prepare_phase_run(
            topic,
            revision=ledger_revision,
            topic_revision=topic_revision,
            from_phase=from_phase,
            to_phase=to_phase,
            carrier_kind="current-topic",
        )
        evidence = prepared["evidence"]
        revision = ledger_revision + 1
        result: dict[str, object] = prepared
        for operation, parameters in (
            ("authorize-phase-carrier", {"carrier_ref": "discussion-task"}),
            ("phase-ready", {"carrier_ref": "discussion-task", "evidence": evidence}),
            ("phase-activate", {"evidence": evidence}),
            (
                "claim-phase-completion",
                {"carrier_ref": "discussion-task", "evidence": evidence},
            ),
            ("complete-phase-run", {"evidence": evidence}),
            ("finalize-phase-run", {"evidence": evidence}),
        ):
            code, result, stderr = self.run_cli(
                self.phase_request(
                    topic,
                    operation,
                    revision,
                    topic_revision=topic_revision,
                    phase_run_id=prepared["phase_run_id"],
                    attempt_id=prepared["attempt_id"],
                    **parameters,
                )
            )
            self.assertEqual(code, 0, stderr)
            revision += 1
        return result, revision, topic_revision + 1

    def test_root_discussion_executes_full_zero_through_four_lifecycle(self) -> None:
        project = self.make_project("root-zero-through-four", git=False)
        topic = self.bootstrap_topic(project)
        ledger_revision = 1
        topic_revision = 1
        phase_result_ids: list[str] = []

        for from_phase, to_phase in ((0, 1), (1, 2), (2, 3), (3, 4)):
            completed, ledger_revision, topic_revision = self.complete_current_topic_phase(
                topic,
                ledger_revision=ledger_revision,
                topic_revision=topic_revision,
                from_phase=from_phase,
                to_phase=to_phase,
            )
            self.assertEqual(completed["current_phase"], to_phase)
            phase_result_ids.append(str(completed["phase_result_id"]))

        self.assertEqual(len(set(phase_result_ids)), 4)
        read_request = self.phase_request(topic, "read-topic", 0)
        for key in ("expected_ledger_revision", "expected_topic_revision", "idempotency_key"):
            read_request.pop(key)
        code, validated, stderr = self.run_cli(read_request)
        self.assertEqual(code, 0, stderr)
        self.assertEqual(validated["state"], "read")
        self.assertEqual(validated["record_revision"], 5)

    def test_change_closure_advances_three_to_four_only_after_finalization(self) -> None:
        project = self.make_project("change-closure-three-to-four", git=False)
        topic = self.bootstrap_topic(project)
        ledger_revision = 1
        topic_revision = 1

        for from_phase, to_phase in ((0, 1), (1, 2), (2, 3)):
            _, ledger_revision, topic_revision = self.complete_current_topic_phase(
                topic,
                ledger_revision=ledger_revision,
                topic_revision=topic_revision,
                from_phase=from_phase,
                to_phase=to_phase,
            )

        prepared = self.prepare_phase_run(
            topic,
            revision=ledger_revision,
            topic_revision=topic_revision,
            from_phase=3,
            to_phase=4,
            carrier_kind="change-closure",
        )
        evidence = prepared["evidence"]
        revision = ledger_revision + 1

        for operation, parameters in (
            ("authorize-phase-carrier", {"carrier_ref": "discussion-task"}),
            ("phase-ready", {"carrier_ref": "discussion-task", "evidence": evidence}),
            ("phase-activate", {"evidence": evidence}),
            (
                "claim-phase-completion",
                {"carrier_ref": "discussion-task", "evidence": evidence},
            ),
            ("complete-phase-run", {"evidence": evidence}),
        ):
            code, _, stderr = self.run_cli(
                self.phase_request(
                    topic,
                    operation,
                    revision,
                    topic_revision=topic_revision,
                    phase_run_id=prepared["phase_run_id"],
                    attempt_id=prepared["attempt_id"],
                    **parameters,
                )
            )
            self.assertEqual(code, 0, stderr)
            revision += 1

            read_request = self.phase_request(topic, "read-topic", 0)
            for key in (
                "expected_ledger_revision",
                "expected_topic_revision",
                "idempotency_key",
            ):
                read_request.pop(key)
            code, current, stderr = self.run_cli(read_request)
            self.assertEqual(code, 0, stderr)
            self.assertEqual(current["current_phase"], 3)

        code, finalized, stderr = self.run_cli(
            self.phase_request(
                topic,
                "finalize-phase-run",
                revision,
                topic_revision=topic_revision,
                phase_run_id=prepared["phase_run_id"],
                attempt_id=prepared["attempt_id"],
                evidence=evidence,
            )
        )
        self.assertEqual(code, 0, stderr)
        self.assertEqual(finalized["current_phase"], 4)

    def test_competing_phase_run_cannot_become_double_active(self) -> None:
        project = self.make_project("no-double-active-phase-run", git=False)
        topic = self.bootstrap_topic(project)
        prepared = self.prepare_phase_run(topic)
        evidence = prepared["evidence"]
        transitions = (
            ("authorize-phase-carrier", {"carrier_ref": "discussion-task"}),
            ("phase-ready", {"carrier_ref": "discussion-task", "evidence": evidence}),
            ("phase-activate", {"evidence": evidence}),
        )
        for revision, (operation, parameters) in enumerate(transitions, start=2):
            code, _, stderr = self.run_cli(
                self.phase_request(
                    topic,
                    operation,
                    revision,
                    phase_run_id=prepared["phase_run_id"],
                    attempt_id=prepared["attempt_id"],
                    **parameters,
                )
            )
            self.assertEqual(code, 0, stderr)

        ledger = Path(str(topic["ledger_path"]))
        before = ledger.read_bytes()
        code, rejected, _ = self.run_cli(
            self.phase_request(
                topic,
                "prepare-phase-run",
                5,
                from_phase=0,
                to_phase=2,
                route="0->2",
                carrier_kind="competing-carrier",
            )
        )
        self.assertEqual(code, 1)
        self.assertEqual(rejected["error"]["code"], "phase_coordination_drift")
        self.assertEqual(ledger.read_bytes(), before)

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
        user_reply: str = "可以，后续阶段自动执行",
        confirmation_intent: str = "continuous",
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
                confirmation_intent=confirmation_intent,
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

    def test_wrapper_pending_impacts_and_direct_to_three_is_rejected(self) -> None:
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
        code, rejected, _ = self.run_cli(
            self.wrapper_phase_request(
                topic,
                3,
                from_phase=1,
                to_phase=3,
                carrier_kind="guided-implementation",
                source_checkpoint_id=str(checkpoint["checkpoint_id"]),
                scope=["skills/design-discussion/scripts"],
            )
        )
        self.assertEqual(code, 1)
        self.assertEqual(rejected["error"]["code"], "invalid_phase_route")

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
            user_reply="可以",
            confirmation_intent="stepwise",
        )
        self.assertEqual(code, 1)
        self.assertEqual(rejected["error"]["code"], "phase_flow_mode_invalid")
        code, authorized, stderr = self.authorize_continuous_flow(
            stage_one,
            ledger_revision=10,
            checkpoint=stage_one_checkpoint,
            phase_result_id=str(stage_one_result["phase_result_id"]),
            user_reply="可以，后续阶段都自动执行。",
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
            confirmation_intent="continuous",
            user_reply="按这个做，后续阶段自动执行",
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

        standalone = self.make_project("wrapper-no-context", git=False)
        before = sorted(standalone.rglob("*"))
        code, located, stderr = self.run_cli(
            {
                "protocol_version": 1,
                "operation": "discover-context",
                "project_path": str(standalone),
            }
        )
        self.assertEqual(code, 0, stderr)
        self.assertEqual(located["context"], "none")
        self.assertEqual(before, sorted(standalone.rglob("*")))

        ambiguous_root = standalone / "docs" / "discussions"
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
            path: path.read_bytes() for path in standalone.rglob("*") if path.is_file()
        }
        code, ambiguous, stderr = self.run_cli(
            {
                "protocol_version": 1,
                "operation": "discover-context",
                "project_path": str(standalone),
            }
        )
        self.assertEqual(code, 0, stderr)
        self.assertEqual(ambiguous["context"], "ambiguous")
        self.assertEqual(
            ambiguous_before,
            {path: path.read_bytes() for path in standalone.rglob("*") if path.is_file()},
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
        legal = {(0, 1), (0, 2), (1, 2), (2, 3), (3, 4)}
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
            "coverage": ("## Relations and Coverage\n\n```yaml\nrecords:\n  []\n```", "## Relations and Coverage\n\n```yaml\nrecords:\n  - relation_id: \"REL-drift\"\n    relation_type: \"absorbs\"\n    source_topic_id: \"TOPIC_ID\"\n    target_topic_id: \"topic-99999999999999999999999999999999\"\n```"),
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
            ledger = Path(str(topic["ledger_path"]))
            topic_path = Path(str(topic["topic_document_path"]))
            ready_ledger = ledger.read_bytes()
            ready_topic = topic_path.read_bytes()
            if dimension == "source":
                topic_path.write_text(topic_path.read_text(encoding="utf-8") + "\ndrift\n", encoding="utf-8")
            else:
                expanded_new = new.replace("TOPIC_ID", str(topic["topic_id"]))
                self.rewrite_ledger_with_valid_digest(
                    ledger,
                    old,
                    expanded_new,
                )
            faulted_ledger = ledger.read_bytes()
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
            self.assertEqual(ledger.read_bytes(), faulted_ledger, dimension)

            if dimension == "source":
                topic_path.write_bytes(ready_topic)
            else:
                self.rewrite_ledger_with_valid_digest(ledger, expanded_new, old)
            self.assertEqual(ledger.read_bytes(), ready_ledger, dimension)
            self.assertEqual(topic_path.read_bytes(), ready_topic, dimension)
            validate = self.phase_request(topic, "read-topic", 0)
            for key in (
                "expected_ledger_revision",
                "expected_topic_revision",
                "idempotency_key",
            ):
                validate.pop(key)
            code, valid, stderr = self.run_cli(validate)
            self.assertEqual(code, 0, stderr)
            self.assertEqual(valid["state"], "read")
            self.assertEqual(valid["ledger_revision"], 4)

            code, _, stderr = self.run_cli(
                self.phase_request(
                    topic,
                    "cancel-phase-run",
                    4,
                    phase_run_id=prepared["phase_run_id"],
                    attempt_id=prepared["attempt_id"],
                    reason="replace the drifted ready attempt",
                )
            )
            self.assertEqual(code, 0, stderr)
            replacement = self.prepare_phase_run(topic, revision=5)
            replacement_evidence = replacement["evidence"]
            for revision, (operation, parameters) in enumerate(
                (
                    ("authorize-phase-carrier", {"carrier_ref": "discussion-task"}),
                    (
                        "phase-ready",
                        {
                            "carrier_ref": "discussion-task",
                            "evidence": replacement_evidence,
                        },
                    ),
                    ("phase-activate", {"evidence": replacement_evidence}),
                ),
                start=6,
            ):
                code, recovered, stderr = self.run_cli(
                    self.phase_request(
                        topic,
                        operation,
                        revision,
                        phase_run_id=replacement["phase_run_id"],
                        attempt_id=replacement["attempt_id"],
                        **parameters,
                    )
                )
                self.assertEqual(code, 0, stderr)
            self.assertEqual(recovered["state"], "active")

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

    def test_document_only_implicit_initialization_rejects_path_bearing_manifest_identities(
        self,
    ) -> None:
        for field in ("project_id", "tree_id"):
            with self.subTest(field=field):
                project = self.make_project(f"document-only-invalid-{field}", git=False)
                seed = self.bootstrap_topic(project)
                Path(str(seed["ledger_path"])).unlink()
                coordination_root = project / ".codex" / "design-discussion" / "v1"
                escaped_root = self.root / f"escaped-ledger-{field}"
                if field == "project_id":
                    malicious_value = str(escaped_root)
                else:
                    tree_parent = (
                        coordination_root
                        / "projects"
                        / str(seed["project_id"])
                        / "trees"
                    )
                    malicious_value = os.path.relpath(escaped_root, tree_parent)

                manifest_path = project / "docs" / "discussions" / ".codex-project.md"
                topic_path = Path(str(seed["topic_document_path"]))
                original_value = str(seed[field])
                for path in (manifest_path, topic_path):
                    path.write_text(
                        path.read_text(encoding="utf-8").replace(
                            f"{field}: {original_value}",
                            f"{field}: {malicious_value}",
                        ),
                        encoding="utf-8",
                    )

                returncode, rejected, _ = self.run_cli(
                    {
                        "protocol_version": 1,
                        "operation": "initialize-document-context",
                        "project_path": str(project),
                        "conversation_ref": "discussion-task",
                        "idempotency_key": str(uuid.uuid4()),
                        "user_authorization": True,
                        "verified_results": [],
                    }
                )

                self.assertEqual(returncode, 1)
                self.assertEqual(
                    rejected["error"]["code"],
                    "context_identity_conflict",
                )
                self.assertFalse(escaped_root.exists())

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

        self.downgrade_ledger_to_v1(ledger, keep_creation_event=True)
        v1_bytes = ledger.read_bytes()
        code, v1_replay, stderr = self.run_cli(request)
        self.assertEqual(code, 0, stderr)
        self.assertTrue(v1_replay["idempotent_replay"])
        self.assertEqual(ledger.read_bytes(), v1_bytes)

        self.replace_recent_events_with_retained_window(
            ledger, str(initialized["topic_id"])
        )
        retained_bytes = ledger.read_bytes()
        code, retained_replay, stderr = self.run_cli(request)
        self.assertEqual(code, 0, stderr)
        self.assertTrue(retained_replay["idempotent_replay"])
        self.assertEqual(retained_replay["ledger_revision"], 201)
        self.assertEqual(ledger.read_bytes(), retained_bytes)

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
        topic_revision: int = 1,
        scope: list[str] | None = None,
        work_snapshot: dict[str, object] | None = None,
        initial_dependencies: list[dict[str, object]] | None = None,
    ) -> dict[str, object]:
        returncode, prepared, stderr = self.run_cli(
            self.handoff_request(
                topic,
                operation="prepare-handoff",
                ledger_revision=ledger_revision,
                topic_revision=topic_revision,
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
                **({"initial_dependencies": initial_dependencies} if initial_dependencies is not None else {}),
            )
        )
        self.assertEqual(returncode, 0, stderr)
        return prepared

    def activate_child_handoff(
        self, topic: dict[str, object], *, initial_dependencies: list[dict[str, object]] | None = None,
    ) -> tuple[dict[str, object], str]:
        """Cross the public handoff seam through later-turn authorization."""
        prepared = self.prepare_child_handoff(
            topic, initial_dependencies=initial_dependencies
        )
        child_ref = "codex-thread:dependency-child"
        code, _, stderr = self.run_cli(
            self.handoff_request(
                topic, operation="bind-handoff", ledger_revision=2,
                handoff_id=prepared["handoff_id"], attempt_id=prepared["attempt_id"],
                conversation_ref=child_ref,
                verified_identity={
                    "project_id": topic["project_id"], "tree_id": topic["tree_id"],
                    "topic_id": prepared["target_topic_id"], "handoff_id": prepared["handoff_id"],
                    "attempt_id": prepared["attempt_id"], "payload_sha256": prepared["payload_sha256"],
                },
            )
        )
        self.assertEqual(code, 0, stderr)
        accept = self.handoff_request(
            topic, operation="accept-handoff", ledger_revision=3, owner_ref=child_ref,
            handoff_id=prepared["handoff_id"], attempt_id=prepared["attempt_id"],
            payload_sha256=prepared["payload_sha256"],
            source_reference_sha256=prepared["authoritative_references_sha256"], turn_number=1,
        )
        accept["actor_topic_id"] = prepared["target_topic_id"]
        code, _, stderr = self.run_cli(accept)
        self.assertEqual(code, 0, stderr)
        authorize = self.handoff_request(
            topic, operation="authorize-handoff-discussion", ledger_revision=4,
            owner_ref=child_ref, handoff_id=prepared["handoff_id"],
            attempt_id=prepared["attempt_id"], turn_number=2,
        )
        authorize["actor_topic_id"] = prepared["target_topic_id"]
        code, _, stderr = self.run_cli(authorize)
        self.assertEqual(code, 0, stderr)
        return prepared, child_ref

    def assert_topic_gate_blocked(
        self,
        result: tuple[int, dict[str, object], str],
        *,
        ledger: Path,
        before: bytes,
        prerequisite_topic_id: str,
    ) -> None:
        code, rejected, stderr = result
        self.assertEqual(code, 1, stderr)
        self.assertEqual(rejected["error"]["code"], "topic_gate_closed")
        context = rejected["error"]["context"]
        self.assertEqual(context["derived_gate_state"], "closed")
        self.assertEqual(
            context["blocked_dependencies"][0]["prerequisite_topic_id"],
            prerequisite_topic_id,
        )
        self.assertEqual(context["blocked_dependencies"][0]["prerequisite_phase"], 0)
        self.assertEqual(context["blocked_dependencies"][0]["prerequisite_state"], "open")
        self.assertEqual(
            context["blocked_dependencies"][0]["waiting_reason"],
            "current required authority is unavailable",
        )
        self.assertEqual(ledger.read_bytes(), before)

    def test_initial_dependency_is_atomic_and_exposes_a_closed_derived_gate(self) -> None:
        project = self.make_project("gated-child-handoff", git=False)
        topic = self.bootstrap_topic(project)
        prepared = self.prepare_child_handoff(
            topic,
            initial_dependencies=[
                {
                    "dependent_endpoint": "source",
                    "prerequisite_topic_ref": "target",
                    "requirement_kind": "confirmed-decision",
                    "requirement_summary": "The child has selected the API shape.",
                }
            ],
        )
        self.assertEqual(len(prepared["initial_dependencies"]), 1)
        code, read, stderr = self.run_cli(
            self.evolution_request(topic, operation="read-topic")
        )
        self.assertEqual(code, 0, stderr)
        self.assertEqual(read["derived_gate_state"], "closed")
        self.assertEqual(read["topic_dependencies"][0]["prerequisite_topic_id"], prepared["target_topic_id"])
        code, evaluation, _ = self.run_cli(
            self.evolution_request(topic, operation="evaluate-topic-gate")
        )
        self.assertEqual(code, 0)
        self.assertEqual(evaluation["state"], "blocked")
        ledger = Path(str(topic["ledger_path"]))
        before = ledger.read_bytes()
        selection = {"dependency_id": read["topic_dependencies"][0]["dependency_id"], "decision_ids": []}
        code, duplicate, _ = self.run_cli(
            self.evolution_request(
                topic, operation="evaluate-topic-gate",
                basis_selection=[selection, selection],
            )
        )
        self.assertEqual(code, 1)
        self.assertEqual(duplicate["error"]["code"], "invalid_request")
        self.assertEqual(ledger.read_bytes(), before)

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
            descriptor = [{"decision_id": decision["decision_id"], "sha256": digest, "summary": decision["summary"]}]
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

    def test_dependent_owner_can_create_and_cancel_a_dependency(self) -> None:
        project = self.make_project("dependency-update", git=False)
        topic = self.bootstrap_topic(project)
        child = self.prepare_child_handoff(topic)
        code, created, stderr = self.run_cli(
            self.evolution_request(
                topic, operation="update-topic-dependency", expected_revision=2, expected_topic_revision=1,
                action="create",
                prerequisite_topic_id=child["target_topic_id"],
                requirement_kind="confirmed-decision", requirement_summary="API shape is chosen.",
            )
        )
        self.assertEqual(code, 0, stderr)
        self.assertEqual(created["derived_gate_state"], "closed")
        code, cancelled, stderr = self.run_cli(
            self.evolution_request(
                topic, operation="update-topic-dependency", expected_revision=3, expected_topic_revision=1,
                action="cancel", dependency_id=created["dependency_id"],
                expected_dependency_revision=1,
            )
        )
        self.assertEqual(code, 0, stderr)
        self.assertEqual(cancelled["derived_gate_state"], "open")

    def test_child_result_freezes_only_current_selected_authority_and_replays(self) -> None:
        project = self.make_project("frozen-child-authority", git=False)
        topic = self.bootstrap_topic(project)
        prepared, child_ref = self.activate_child_handoff(topic)
        ledger = Path(str(topic["ledger_path"]))
        before = ledger.read_bytes()
        invalid = self.handoff_request(
            topic, operation="submit-child-result", ledger_revision=5,
            owner_ref=child_ref, handoff_id=prepared["handoff_id"],
            attempt_id=prepared["attempt_id"], result_scope=["api"], summary="Use typed requests.",
            authority_selection={
                "authority_kind": "confirmed-decision", "authority_identity": None,
                "decision_ids": ["D-not-current"],
            },
        )
        invalid["actor_topic_id"] = prepared["target_topic_id"]
        code, rejected, _ = self.run_cli(invalid)
        self.assertEqual(code, 1)
        self.assertEqual(rejected["error"]["code"], "topic_dependency_evidence_unavailable")
        self.assertEqual(ledger.read_bytes(), before)

        valid = dict(invalid)
        valid["idempotency_key"] = str(uuid.uuid4())
        valid.pop("authority_selection")
        code, claimed, stderr = self.run_cli(valid)
        self.assertEqual(code, 0, stderr)
        code, replayed, stderr = self.run_cli(valid)
        self.assertEqual(code, 0, stderr)
        self.assertTrue(replayed["idempotent_replay"])
        self.assertEqual(replayed["child_result_id"], claimed["child_result_id"])

    def test_ticket07_child_result_requires_selection_when_current_authority_exists(self) -> None:
        project = self.make_project("ticket07-child-result-authority", git=False)
        topic = self.bootstrap_topic(project)
        prepared, child_ref = self.activate_child_handoff(topic)
        self.complete_update(
            project, topic, ledger_revision=5, topic_revision=1,
            mutation={
                "type": "confirm-decision",
                "summary": "Use typed requests.",
                "rationale": "The child has selected its public authority.",
            },
        )
        ledger = Path(str(topic["ledger_path"]))
        pending_marker = "## Pending Items\n\n"
        pending_start = ledger.read_text(encoding="utf-8").index(pending_marker)
        pending_end = ledger.read_text(encoding="utf-8").index("\n## ", pending_start + len(pending_marker))
        current = ledger.read_text(encoding="utf-8")
        pending = current[pending_start:pending_end]
        self.rewrite_ledger_with_valid_digest(
            ledger, pending,
            pending.replace(str(topic["topic_id"]), str(prepared["target_topic_id"])),
        )
        before = ledger.read_bytes()
        request = self.handoff_request(
            topic, operation="submit-child-result", ledger_revision=7,
            topic_revision=1, owner_ref=child_ref,
            handoff_id=prepared["handoff_id"], attempt_id=prepared["attempt_id"],
            result_scope=["api"], summary="Use typed requests.",
        )
        request["actor_topic_id"] = prepared["target_topic_id"]
        code, rejected, _ = self.run_cli(request)
        self.assertEqual(code, 1)
        self.assertEqual(rejected["error"]["code"], "topic_dependency_authority_selection_required")
        self.assertEqual(ledger.read_bytes(), before)

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

    def test_ticket07_stale_checkpoint_release_is_rejected_via_cli(self) -> None:
        project = self.make_project("ticket07-stale-checkpoint-release", git=False)
        topic = self.bootstrap_topic(project)
        checkpoint = self.publish_non_git_stage_entry_checkpoint(topic, ledger_revision=1)
        ledger = Path(str(topic["ledger_path"]))
        frontmatter, records = PROTOCOL._load_records(ledger)
        dependent_id = "topic-" + "1" * 32
        records["Current Topics"].append({"topic_id": dependent_id, "record_revision": 1, "root_slug": "dependent", "parent_topic_id": str(topic["topic_id"]), "current_phase": 0, "phase_state": "active", "review_state": "unreviewed", "topic_state": "open", "topic_document_path": None})
        records["Conversation Bindings"].append({"topic_id": dependent_id, "conversation_ref": "codex-thread:dependent", "binding_state": "active", "record_revision": 1, "handoff_id": None, "attempt_id": None})
        dependency_id = "DEP-" + "a" * 32
        records["Topic Dependencies"].append({"dependency_id": dependency_id, "record_revision": 1, "dependent_topic_id": dependent_id, "prerequisite_topic_id": str(topic["topic_id"]), "requirement_kind": "phase-0-checkpoint", "requirement_summary": "The root checkpoint is current.", "relation_state": "active", "gate_state": "closed", "accepted_basis_json": None, "gate_reason_json": PROTOCOL._canonical_json({"kind": "explicit-create", "dependency_update_id": "00000000-0000-4000-8000-000000000000", "ledger_revision": 1})})
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
        records["Current Topics"].append({"topic_id": dependent_id, "record_revision": 1, "root_slug": "dependent", "parent_topic_id": str(topic["topic_id"]), "current_phase": 1, "phase_state": "active", "review_state": "unreviewed", "topic_state": "open", "topic_document_path": None})
        records["Conversation Bindings"].append({"topic_id": dependent_id, "conversation_ref": "codex-thread:dependent", "binding_state": "active", "record_revision": 1, "handoff_id": None, "attempt_id": None})
        dependency_id = "DEP-" + "b" * 32
        records["Topic Dependencies"].append({"dependency_id": dependency_id, "record_revision": 1, "dependent_topic_id": dependent_id, "prerequisite_topic_id": str(topic["topic_id"]), "requirement_kind": "phase-1-result", "requirement_summary": "The root Phase 1 result is current.", "relation_state": "active", "gate_state": "closed", "accepted_basis_json": None, "gate_reason_json": PROTOCOL._canonical_json({"kind": "explicit-create", "dependency_update_id": "00000000-0000-4000-8000-000000000000", "ledger_revision": 1})})
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
        before = ledger.read_bytes()
        code, replay, stderr = self.run_cli(reopen)
        self.assertEqual(code, 0, stderr)
        self.assertTrue(replay["idempotent_replay"])
        self.assertEqual(ledger.read_bytes(), before)

    def test_ticket07_upstream_change_skips_phase2_dependents_via_cli(self) -> None:
        project = self.make_project("ticket07-phase2-invalidation", git=False)
        topic = self.bootstrap_topic(project)
        ledger = Path(str(topic["ledger_path"]))
        a_id = str(topic["topic_id"])
        b_id = "topic-" + "b" * 32
        decision = {"decision_id": "D-a", "summary": "Keep A.", "rationale": "Current authority.", "state": "confirmed", "evolution": "confirmed"}
        digest = hashlib.sha256(PROTOCOL._canonical_json(decision).encode("utf-8")).hexdigest()
        authority = [{"decision_id": "D-a", "sha256": digest, "summary": "Keep A."}]
        dependency_id = "DEP-" + "3" * 32
        basis = {"basis_version": 1, "dependency_id": dependency_id, "prerequisite_topic_id": a_id, "requirement_kind": "confirmed-decision", "decision_authority": [{"decision_id": "D-a", "sha256": digest}], "authority": {"decision_set_digest": hashlib.sha256(PROTOCOL._canonical_json(authority).encode("utf-8")).hexdigest()}}
        frontmatter, records = PROTOCOL._load_records(ledger)
        records["Current Topics"].append({"topic_id": b_id, "record_revision": 1, "root_slug": "topic-b", "parent_topic_id": a_id, "current_phase": 2, "phase_state": "active", "review_state": "unreviewed", "topic_state": "open", "topic_document_path": None})
        records["Pending Items"].append({"item_id": "D-a", "item_kind": "decision", "topic_id": a_id, "data_json": PROTOCOL._canonical_json(decision)})
        records["Topic Dependencies"].append({"dependency_id": dependency_id, "record_revision": 2, "dependent_topic_id": b_id, "prerequisite_topic_id": a_id, "requirement_kind": "confirmed-decision", "requirement_summary": "A remains current.", "relation_state": "active", "gate_state": "open", "accepted_basis_json": PROTOCOL._canonical_json(basis), "gate_reason_json": PROTOCOL._canonical_json({"kind": "atomic-release", "release_id": "00000000-0000-4000-8000-000000000000", "ledger_revision": 1})})
        ledger.write_bytes(PROTOCOL._render_records_ledger(frontmatter, records))
        _, before_records = PROTOCOL._load_records(ledger)
        before_dependency = next(item for item in before_records["Topic Dependencies"] if item["dependency_id"] == dependency_id)
        changed, ledger_revision, topic_revision = self.complete_update(
            project, topic, ledger_revision=1, topic_revision=1,
            mutation={"type": "change-direction", "summary": "Replace A.", "affected_decision_ids": ["D-a"]},
        )
        code, _, stderr = self.run_cli(self.evolution_request(
            topic, operation="prepare-topic-update", expected_revision=ledger_revision,
            expected_topic_revision=topic_revision,
            mutation={"type": "resolve-impact", "impact_id": changed["impact_ids"][0], "decision_id": "D-a", "action": "replace", "summary": "A was replaced."},
        ))
        self.assertEqual(code, 0, stderr)
        _, after_records = PROTOCOL._load_records(ledger)
        after_dependency = next(item for item in after_records["Topic Dependencies"] if item["dependency_id"] == dependency_id)
        self.assertEqual(after_dependency, before_dependency)

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

    def test_child_phase_rejects_root_manifest_slug_retarget(self) -> None:
        project = self.make_project("child-phase-slug-retarget", git=False)
        topic = self.bootstrap_topic(project)
        prepared = self.prepare_child_handoff(topic)
        child_ref = "codex-thread:retargeted-child"

        returncode, _, stderr = self.run_cli(
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
        self.assertEqual(self.run_cli(accept)[0], 0)
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
        self.assertEqual(self.run_cli(authorize)[0], 0)

        topic_path = Path(str(topic["topic_document_path"]))
        sibling_path = project / "docs" / "discussions" / "sibling-topic" / "topic.md"
        sibling_path.parent.mkdir(parents=True)
        sibling_path.write_bytes(topic_path.read_bytes())
        manifest_path = project / "docs" / "discussions" / ".codex-project.md"
        manifest_path.write_text(
            manifest_path.read_text(encoding="utf-8").replace(
                "root_slug: checkout-redesign",
                "root_slug: sibling-topic",
            ),
            encoding="utf-8",
        )
        request = self.phase_request(
            topic,
            "prepare-phase-run",
            5,
            from_phase=0,
            to_phase=2,
            route="0->2",
            carrier_kind="current-topic",
        )
        request["actor_topic_id"] = prepared["target_topic_id"]
        request["actor_conversation_ref"] = child_ref

        returncode, rejected, _ = self.run_cli(request)

        self.assertEqual(returncode, 1)
        self.assertEqual(rejected["error"]["code"], "state_corrupt")

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
            operation="read-topic",
            owner_ref=second_bound["conversation_ref"],
        )
        returncode, valid, stderr = self.run_cli(validate)
        self.assertEqual(returncode, 0, stderr)
        self.assertEqual(valid["state"], "read")
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
        self.assertEqual(claimed["frozen_authority"]["authority_kind"], "none")
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

    def test_fully_absorbed_child_implementation_records_no_code_integration_phase_three(self) -> None:
        project = self.make_project("no-code-integration", git=False)
        topic = self.bootstrap_topic(project)
        prepared = self.prepare_child_handoff(topic, scope=["api"])
        child_ref = "codex-thread:no-code-child"

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
        self.assertEqual(self.run_cli(accept)[0], 0)
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
        self.assertEqual(self.run_cli(authorize)[0], 0)

        def complete_phase(
            *, actor_topic_id: str, owner_ref: str, ledger_revision: int,
            topic_revision: int, from_phase: int, to_phase: int,
        ) -> tuple[dict[str, object], int, int]:
            request = self.phase_request(
                topic,
                "prepare-phase-run",
                ledger_revision,
                topic_revision=topic_revision,
                from_phase=from_phase,
                to_phase=to_phase,
                route=f"{from_phase}->{to_phase}",
                carrier_kind="current-topic",
            )
            request["actor_topic_id"] = actor_topic_id
            request["actor_conversation_ref"] = owner_ref
            code, run, stderr = self.run_cli(request)
            self.assertEqual(code, 0, stderr)
            evidence = run["evidence"]
            current_revision = ledger_revision + 1
            for operation, parameters in (
                ("authorize-phase-carrier", {"carrier_ref": owner_ref}),
                ("phase-ready", {"carrier_ref": owner_ref, "evidence": evidence}),
                ("phase-activate", {"evidence": evidence}),
                ("claim-phase-completion", {"carrier_ref": owner_ref, "evidence": evidence}),
                ("complete-phase-run", {"evidence": evidence}),
                ("finalize-phase-run", {"evidence": evidence}),
            ):
                phase_request = self.phase_request(
                    topic,
                    operation,
                    current_revision,
                    topic_revision=topic_revision,
                    phase_run_id=run["phase_run_id"],
                    attempt_id=run["attempt_id"],
                    **parameters,
                )
                phase_request["actor_topic_id"] = actor_topic_id
                phase_request["actor_conversation_ref"] = owner_ref
                code, result, stderr = self.run_cli(phase_request)
                self.assertEqual(code, 0, stderr)
                current_revision += 1
            return result, current_revision, topic_revision + 1

        child_phase_two, revision, child_revision = complete_phase(
            actor_topic_id=str(prepared["target_topic_id"]), owner_ref=child_ref,
            ledger_revision=5, topic_revision=1, from_phase=0, to_phase=2,
        )
        self.assertEqual(child_phase_two["current_phase"], 2)
        child_phase_three, revision, child_revision = complete_phase(
            actor_topic_id=str(prepared["target_topic_id"]), owner_ref=child_ref,
            ledger_revision=revision, topic_revision=child_revision, from_phase=2, to_phase=3,
        )
        self.assertEqual(child_phase_three["current_phase"], 3)

        submit = self.handoff_request(
            topic,
            operation="submit-child-result",
            ledger_revision=revision,
            owner_ref=child_ref,
            topic_revision=child_revision,
            handoff_id=prepared["handoff_id"],
            attempt_id=prepared["attempt_id"],
            result_scope=["api"],
            summary="Child implementation covers the parent API scope.",
        )
        submit["actor_topic_id"] = prepared["target_topic_id"]
        code, claim, stderr = self.run_cli(submit)
        self.assertEqual(code, 0, stderr)
        revision += 1
        code, absorbed, stderr = self.run_cli(
            self.handoff_request(
                topic,
                operation="record-child-result",
                ledger_revision=revision,
                handoff_id=prepared["handoff_id"],
                child_result_id=claim["child_result_id"],
                effect="absorb",
            )
        )
        self.assertEqual(code, 0, stderr)
        revision += 1

        parent_phase_one, revision, parent_revision = complete_phase(
            actor_topic_id=str(topic["topic_id"]), owner_ref="discussion-task",
            ledger_revision=revision, topic_revision=1, from_phase=0, to_phase=1,
        )
        self.assertEqual(parent_phase_one["current_phase"], 1)
        checkpoint = self.publish_non_git_stage_entry_checkpoint(
            topic, ledger_revision=revision, topic_revision=parent_revision
        )
        revision += 2

        code, run, stderr = self.run_cli(
            self.phase_request(
                topic,
                "prepare-no-code-integration-run",
                revision,
                topic_revision=parent_revision,
                source_checkpoint_id=checkpoint["checkpoint_id"],
                scope=["api"],
                absorbed_relation_ids=[absorbed["relation_id"]],
                child_phase_result_ids=[child_phase_three["phase_result_id"]],
            )
        )
        self.assertEqual(code, 0, stderr)
        self.assertEqual(run["implementation_mode"], "no-code-integration")
        evidence = run["evidence"]
        revision += 1
        for operation, parameters in (
            ("authorize-phase-carrier", {"carrier_ref": "discussion-task"}),
            (
                "claim-phase-carrier",
                {
                    "carrier_ref": "discussion-task",
                    "source_checkpoint_id": checkpoint["checkpoint_id"],
                    "source_checkpoint_identity": checkpoint["snapshot_digest"],
                },
            ),
            ("phase-ready", {"carrier_ref": "discussion-task", "evidence": evidence}),
            ("phase-activate", {"evidence": evidence}),
            ("claim-phase-completion", {"carrier_ref": "discussion-task", "evidence": evidence}),
            ("complete-phase-run", {"evidence": evidence}),
            ("finalize-phase-run", {"evidence": evidence}),
        ):
            code, result, stderr = self.run_cli(
                self.phase_request(
                    topic,
                    operation,
                    revision,
                    topic_revision=parent_revision,
                    phase_run_id=run["phase_run_id"],
                    attempt_id=run["attempt_id"],
                    **parameters,
                )
            )
            self.assertEqual(code, 0, stderr)
            revision += 1
        self.assertEqual(result["current_phase"], 3)
        self.assertEqual(result["implementation_mode"], "no-code-integration")
        read = self.run_cli(
            self.evolution_request(
                topic,
                operation="read-phase-run",
                phase_run_id=run["phase_run_id"],
            )
        )[1]
        self.assertEqual(
            read["phase_run"]["implementation_mode"],
            "no-code-integration",
        )

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
        topic_revision: int = 1,
    ) -> dict[str, object]:
        returncode, published, stderr = self.run_cli(
            self.checkpoint_request(
                topic,
                operation="publish-git-checkpoint",
                ledger_revision=ledger_revision,
                topic_revision=topic_revision,
                checkpoint_id=prepared["checkpoint_id"],
                expected_checkpoint_revision=prepared["checkpoint_record_revision"],
            )
        )
        self.assertEqual(returncode, 0, stderr)
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
        returncode, applied, stderr = self.run_cli(
            self.evolution_request(
                topic,
                operation="apply-document-write",
                expected_revision=ledger_revision + 1,
                expected_topic_revision=topic_revision + 1,
                owner_ref=owner_ref,
                document_write_id=prepared["document_write_id"],
            )
        )
        self.assertEqual(returncode, 0, stderr)
        self.assertTrue(applied["document_verified"])
        self.assertEqual(applied["state"], "completed")
        return prepared, ledger_revision + 2, topic_revision + 1

    def test_confirmed_decision_is_applied_with_one_atomic_document_write(self) -> None:
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

        apply_request = self.evolution_request(
            topic,
            operation="apply-document-write",
            expected_revision=2,
            expected_topic_revision=2,
            document_write_id=prepared["document_write_id"],
        )
        returncode, applied, stderr = self.run_cli(apply_request)
        self.assertEqual(returncode, 0, stderr)
        self.assertEqual(applied["state"], "completed")
        self.assertTrue(applied["document_verified"])
        self.assertEqual(payload_path.read_bytes(), payload_before)
        topic_text = Path(str(topic["topic_document_path"])).read_text(encoding="utf-8")
        self.assertIn(str(prepared["decision_id"]), topic_text)
        self.assertIn("Use a single durable ledger.", topic_text)

        returncode, inspected, stderr = self.run_cli(
            self.evolution_request(
                topic,
            operation="read-topic",
            )
        )
        self.assertEqual(returncode, 0, stderr)
        self.assertEqual(inspected["decisions"][0]["decision_id"], prepared["decision_id"])
        self.assertEqual(inspected["pending_document_writes"][0]["state"], "completed")

    def test_apply_document_write_rejects_manifest_slug_retarget_without_mutating_either_topic(
        self,
    ) -> None:
        project = self.make_project("document-write-slug-retarget", git=False)
        topic = self.bootstrap_topic(project)
        topic_path = Path(str(topic["topic_document_path"]))
        original_bytes = topic_path.read_bytes()
        sibling_path = project / "docs" / "discussions" / "sibling-topic" / "topic.md"
        sibling_path.parent.mkdir(parents=True)
        sibling_path.write_bytes(original_bytes)

        returncode, prepared, stderr = self.run_cli(
            self.evolution_request(
                topic,
                operation="prepare-topic-update",
                expected_revision=1,
                mutation={
                    "type": "confirm-decision",
                    "summary": "Keep one authoritative topic path.",
                    "rationale": "A mutable manifest must not retarget a pending write.",
                },
            )
        )
        self.assertEqual(returncode, 0, stderr)

        manifest_path = project / "docs" / "discussions" / ".codex-project.md"
        manifest_path.write_text(
            manifest_path.read_text(encoding="utf-8").replace(
                "root_slug: checkout-redesign",
                "root_slug: sibling-topic",
            ),
            encoding="utf-8",
        )

        returncode, rejected, _ = self.run_cli(
            self.evolution_request(
                topic,
                operation="apply-document-write",
                expected_revision=2,
                expected_topic_revision=2,
                document_write_id=prepared["document_write_id"],
            )
        )

        self.assertEqual(returncode, 1)
        self.assertEqual(rejected["error"]["code"], "state_corrupt")
        self.assertEqual(topic_path.read_bytes(), original_bytes)
        self.assertEqual(sibling_path.read_bytes(), original_bytes)

    def test_apply_document_write_rejects_ledger_topic_path_drift(self) -> None:
        project = self.make_project("document-write-ledger-path-drift", git=False)
        topic = self.bootstrap_topic(project)
        topic_path = Path(str(topic["topic_document_path"]))
        original_bytes = topic_path.read_bytes()
        returncode, prepared, stderr = self.run_cli(
            self.evolution_request(
                topic,
                operation="prepare-topic-update",
                expected_revision=1,
                mutation={
                    "type": "confirm-decision",
                    "summary": "Bind writes to ledger authority.",
                    "rationale": "The apply step must reject a changed topic path.",
                },
            )
        )
        self.assertEqual(returncode, 0, stderr)
        ledger_path = Path(str(topic["ledger_path"]))
        sibling_path = topic_path.parent.parent / "sibling-topic" / "topic.md"
        self.rewrite_ledger_with_valid_digest(
            ledger_path,
            f'topic_document_path: "{topic_path}"',
            f'topic_document_path: "{sibling_path}"',
        )

        returncode, rejected, _ = self.run_cli(
            self.evolution_request(
                topic,
                operation="apply-document-write",
                expected_revision=2,
                expected_topic_revision=2,
                document_write_id=prepared["document_write_id"],
            )
        )

        self.assertEqual(returncode, 1)
        self.assertEqual(rejected["error"]["code"], "state_corrupt")
        self.assertEqual(topic_path.read_bytes(), original_bytes)

    def test_confirming_decision_answers_active_question_and_allows_the_next_question(self) -> None:
        project = self.make_project("decision-answers-question", git=True)
        topic = self.bootstrap_topic(project)
        question, ledger_revision, topic_revision = self.complete_update(
            project,
            topic,
            ledger_revision=1,
            topic_revision=1,
            mutation={
                "type": "set-active-question",
                "prompt": "Which durable write boundary should we use?",
                "recommendation": "Reuse the prepared topic update.",
                "reason": "It keeps the ledger and document update atomic.",
            },
        )
        decision, ledger_revision, topic_revision = self.complete_update(
            project,
            topic,
            ledger_revision=ledger_revision,
            topic_revision=topic_revision,
            mutation={
                "type": "confirm-decision",
                "summary": "Reuse the prepared topic update.",
                "rationale": "It preserves one durable transaction.",
            },
        )

        returncode, readback, stderr = self.run_cli(
            self.evolution_request(topic, operation="read-topic")
        )
        self.assertEqual(returncode, 0, stderr)
        self.assertEqual(readback["active_question_count"], 0)
        self.assertIsNone(readback["active_question"])
        self.assertEqual(readback["questions"], [{
            "question_id": question["question_id"],
            "prompt": "Which durable write boundary should we use?",
            "recommendation": "Reuse the prepared topic update.",
            "reason": "It keeps the ledger and document update atomic.",
            "state": "answered",
            "answered_by_decision_id": decision["decision_id"],
        }])
        topic_text = Path(str(topic["topic_document_path"])).read_text(encoding="utf-8")
        pending_questions = topic_text.split("## Pending Questions\n\n", 1)[1].split(
            "\n\n## Decision Evolution", 1
        )[0]
        self.assertNotIn(str(question["question_id"]), pending_questions)

        next_question, _, _ = self.complete_update(
            project,
            topic,
            ledger_revision=ledger_revision,
            topic_revision=topic_revision,
            mutation={
                "type": "set-active-question",
                "prompt": "Which review actor owns candidate acceptance?",
                "recommendation": "The Originating Task.",
                "reason": "It owns supervision and integration.",
            },
        )
        self.assertRegex(str(next_question["question_id"]), r"^Q-[0-9a-f]{32}$")

    def test_independent_decisions_preserve_zero_and_suspended_questions(self) -> None:
        project = self.make_project("independent-decisions", git=False)
        topic = self.bootstrap_topic(project)
        independent, ledger_revision, topic_revision = self.complete_update(
            project,
            topic,
            ledger_revision=1,
            topic_revision=1,
            mutation={
                "type": "confirm-decision",
                "summary": "Record an independent choice.",
                "rationale": "No question is currently active.",
            },
        )
        question, ledger_revision, topic_revision = self.complete_update(
            project,
            topic,
            ledger_revision=ledger_revision,
            topic_revision=topic_revision,
            mutation={
                "type": "set-active-question",
                "prompt": "Should this question remain visible while suspended?",
                "recommendation": "Yes.",
                "reason": "It remains unfinished work.",
            },
        )
        _, ledger_revision, topic_revision = self.complete_update(
            project,
            topic,
            ledger_revision=ledger_revision,
            topic_revision=topic_revision,
            mutation={"type": "insert-idea", "summary": "Consider a separate option."},
        )
        suspended_decision, _, _ = self.complete_update(
            project,
            topic,
            ledger_revision=ledger_revision,
            topic_revision=topic_revision,
            mutation={
                "type": "confirm-decision",
                "summary": "Keep the inserted idea separate.",
                "rationale": "It does not resolve the suspended question.",
            },
        )

        returncode, readback, stderr = self.run_cli(
            self.evolution_request(topic, operation="read-topic")
        )
        self.assertEqual(returncode, 0, stderr)
        self.assertEqual(
            {item["decision_id"] for item in readback["decisions"]},
            {independent["decision_id"], suspended_decision["decision_id"]},
        )
        self.assertEqual(readback["questions"][0]["state"], "suspended")
        self.assertNotIn("answered_by_decision_id", readback["questions"][0])
        topic_text = Path(str(topic["topic_document_path"])).read_text(encoding="utf-8")
        pending_questions = topic_text.split("## Pending Questions\n\n", 1)[1].split(
            "\n\n## Decision Evolution", 1
        )[0]
        self.assertIn(str(question["question_id"]), pending_questions)
        self.assertIn("[suspended]", pending_questions)

    def test_confirm_decision_replay_does_not_consume_a_later_question(self) -> None:
        project = self.make_project("decision-replay", git=True)
        topic = self.bootstrap_topic(project)
        first_question, ledger_revision, topic_revision = self.complete_update(
            project,
            topic,
            ledger_revision=1,
            topic_revision=1,
            mutation={
                "type": "set-active-question",
                "prompt": "Which first question should the decision answer?",
                "recommendation": "Answer this one.",
                "reason": "It is active when the decision is confirmed.",
            },
        )
        decision_request = self.evolution_request(
            topic,
            operation="prepare-topic-update",
            expected_revision=ledger_revision,
            expected_topic_revision=topic_revision,
            idempotency_key=str(uuid.uuid4()),
            mutation={
                "type": "confirm-decision",
                "summary": "Answer only the current question.",
                "rationale": "Idempotent replay preserves the original transition.",
            },
        )
        code, decision, stderr = self.run_cli(decision_request)
        self.assertEqual(code, 0, stderr)
        code, _, stderr = self.run_cli(
            self.evolution_request(
                topic,
                operation="apply-document-write",
                expected_revision=ledger_revision + 1,
                expected_topic_revision=topic_revision + 1,
                document_write_id=decision["document_write_id"],
            )
        )
        self.assertEqual(code, 0, stderr)
        second_question, _, _ = self.complete_update(
            project,
            topic,
            ledger_revision=ledger_revision + 2,
            topic_revision=topic_revision + 1,
            mutation={
                "type": "set-active-question",
                "prompt": "Which later question must remain active?",
                "recommendation": "Leave it active.",
                "reason": "The earlier decision cannot answer it on replay.",
            },
        )

        code, replay, stderr = self.run_cli(decision_request)
        self.assertEqual(code, 0, stderr)
        self.assertTrue(replay["idempotent_replay"])
        self.assertEqual(replay["decision_id"], decision["decision_id"])
        code, readback, stderr = self.run_cli(
            self.evolution_request(topic, operation="read-topic")
        )
        self.assertEqual(code, 0, stderr)
        questions = {item["question_id"]: item for item in readback["questions"]}
        self.assertEqual(questions[first_question["question_id"]]["state"], "answered")
        self.assertEqual(
            questions[first_question["question_id"]]["answered_by_decision_id"],
            decision["decision_id"],
        )
        self.assertEqual(questions[second_question["question_id"]]["state"], "active")

    def test_multi_active_questions_reject_decision_without_side_effects(self) -> None:
        project = self.make_project("corrupt-multiple-active", git=True)
        topic = self.bootstrap_topic(project)
        _, ledger_revision, topic_revision = self.complete_update(
            project,
            topic,
            ledger_revision=1,
            topic_revision=1,
            mutation={
                "type": "set-active-question",
                "prompt": "Which question is authoritative?",
                "recommendation": "There must be one.",
                "reason": "The protocol has a single-question loop.",
            },
        )
        ledger_path = Path(str(topic["ledger_path"]))
        frontmatter, records = PROTOCOL._load_records(ledger_path)
        original = next(record for record in records["Pending Items"] if record["item_kind"] == "question")
        duplicate_data = json.loads(original["data_json"])
        duplicate_data["question_id"] = "Q-corrupt-second-active"
        records["Pending Items"].append(
            {
                "item_id": duplicate_data["question_id"],
                "item_kind": "question",
                "topic_id": topic["topic_id"],
                "data_json": json.dumps(duplicate_data, sort_keys=True, separators=(",", ":")),
            }
        )
        ledger_path.write_bytes(PROTOCOL._render_records_ledger(frontmatter, records))
        before_ledger = ledger_path.read_bytes()
        before_document = Path(str(topic["topic_document_path"])).read_bytes()
        before_pending_writes = list(records["Pending Document Writes"])

        code, rejected, _ = self.run_cli(
            self.evolution_request(
                topic,
                operation="prepare-topic-update",
                expected_revision=ledger_revision,
                expected_topic_revision=topic_revision,
                mutation={
                    "type": "confirm-decision",
                    "summary": "Do not mutate corrupt state.",
                    "rationale": "Multiple active questions are invalid authority state.",
                },
            )
        )
        self.assertEqual(code, 1)
        self.assertEqual(rejected["error"]["code"], "state_corrupt")
        self.assertEqual(ledger_path.read_bytes(), before_ledger)
        self.assertEqual(Path(str(topic["topic_document_path"])).read_bytes(), before_document)
        _, after_records = PROTOCOL._load_records(ledger_path)
        self.assertFalse(any(item["item_kind"] == "decision" for item in after_records["Pending Items"]))
        self.assertEqual(after_records["Pending Document Writes"], before_pending_writes)

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
                operation="apply-document-write",
                expected_revision=2,
                expected_topic_revision=2,
                document_write_id=first["document_write_id"],
            )
        )
        self.assertEqual(returncode, 0, stderr)
        self.assertEqual(reconciled["state"], "completed")
        self.assertTrue(reconciled["document_verified"])

    def test_orphan_payload_is_digest_bound_and_adopted_by_exact_prepare_replay(self) -> None:
        project = self.make_project("orphan-prepare-replay", git=True)
        topic = self.bootstrap_topic(project)
        request = self.evolution_request(
            topic,
            operation="prepare-topic-update",
            expected_revision=1,
            expected_topic_revision=1,
            mutation={
                "type": "confirm-decision",
                "summary": "Recover the published payload.",
                "rationale": "The ledger remains the only authority.",
            },
        )

        failed_code, failed, _ = self.run_cli(
            request, failpoint="topic-update-ledger-replace"
        )
        self.assertEqual(failed_code, 1)
        self.assertEqual(failed["error"]["code"], "internal_error")
        self.assertEqual(failed["error"]["cause"], "OSError")
        self.assertEqual(failed["project_id"], topic["project_id"])
        self.assertEqual(failed["tree_id"], topic["tree_id"])
        self.assertEqual(failed["topic_id"], topic["topic_id"])
        payload_dir = Path(str(topic["ledger_path"])).parent / "pending-writes"
        payloads = list(payload_dir.glob("*.payload"))
        self.assertEqual(len(payloads), 1)
        orphan_bytes = payloads[0].read_bytes()

        validate_code, invalid, _ = self.run_cli(
            self.evolution_request(topic, operation="read-topic")
        )
        self.assertEqual(validate_code, 1)
        self.assertEqual(invalid["error"]["code"], "orphaned_document_write")

        conflicting = dict(request)
        conflicting["mutation"] = {
            "type": "confirm-decision",
            "summary": "Different bytes under the same key.",
            "rationale": "This must not take over the orphan.",
        }
        conflict_code, conflict, _ = self.run_cli(conflicting)
        self.assertEqual(conflict_code, 1)
        self.assertEqual(conflict["error"]["code"], "document_write_orphan_conflict")
        self.assertEqual(payloads[0].read_bytes(), orphan_bytes)

        recovered_code, recovered, recovered_stderr = self.run_cli(request)
        self.assertEqual(recovered_code, 0, recovered_stderr)
        self.assertTrue(recovered["recovered_orphan"])
        self.assertEqual(Path(str(recovered["payload_path"])).read_bytes(), orphan_bytes)
        replay_code, replay, replay_stderr = self.run_cli(request)
        self.assertEqual(replay_code, 0, replay_stderr)
        self.assertTrue(replay["idempotent_replay"])
        self.assertEqual(replay["document_write_id"], recovered["document_write_id"])

    def test_unrelated_orphan_blocks_a_new_prepare_without_publishing_another_payload(self) -> None:
        project = self.make_project("unrelated-orphan", git=True)
        topic = self.bootstrap_topic(project)
        payload_dir = Path(str(topic["ledger_path"])).parent / "pending-writes"
        payload_dir.mkdir()
        orphan = payload_dir / "DW-00000000000000000000000000000000.payload"
        orphan.write_bytes(b"unowned\n")

        code, response, _ = self.run_cli(
            self.evolution_request(
                topic,
                operation="prepare-topic-update",
                expected_revision=1,
                expected_topic_revision=1,
                mutation={
                    "type": "confirm-decision",
                    "summary": "Do not multiply orphans.",
                    "rationale": "Recovery must be explicit.",
                },
            )
        )

        self.assertEqual(code, 1)
        self.assertEqual(response["error"]["code"], "orphaned_document_write")
        self.assertEqual(list(payload_dir.iterdir()), [orphan])

    def test_only_the_topic_owner_can_apply_a_pending_write(self) -> None:
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

        returncode, conflict, _ = self.run_cli(
            self.evolution_request(
                topic,
                operation="apply-document-write",
                expected_revision=2,
                expected_topic_revision=2,
                owner_ref="foreign-task",
                document_write_id=prepared["document_write_id"],
            )
        )
        self.assertEqual(returncode, 1)
        self.assertEqual(conflict["error"]["code"], "document_ownership_conflict")
        returncode, applied, stderr = self.run_cli(
            self.evolution_request(
                topic,
                operation="apply-document-write",
                expected_revision=2,
                expected_topic_revision=2,
                document_write_id=prepared["document_write_id"],
            )
        )
        self.assertEqual(returncode, 0, stderr)
        self.assertEqual(applied["state"], "completed")

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
                    self.evolution_request(topic, operation="read-topic")
                )
                self.assertEqual(returncode, 1)
                expected = (
                    "document_write_payload_damaged"
                    if damage_kind == "damaged"
                    else "orphaned_document_write"
                )
                self.assertEqual(response["error"]["code"], expected)

    def test_apply_adopts_payload_bytes_written_before_the_result_was_recorded(self) -> None:
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
        Path(str(topic["topic_document_path"])).write_bytes(
            Path(str(prepared["payload_path"])).read_bytes()
        )

        returncode, completed, stderr = self.run_cli(
            self.evolution_request(
                topic,
                operation="apply-document-write",
                expected_revision=2,
                expected_topic_revision=2,
                document_write_id=prepared["document_write_id"],
            )
        )
        self.assertEqual(returncode, 0, stderr)
        self.assertEqual(completed["state"], "completed")
        self.assertTrue(completed["document_verified"])

    def test_non_git_topic_update_uses_the_same_atomic_write(self) -> None:
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
        topic_text = Path(str(topic["topic_document_path"])).read_text(encoding="utf-8")
        self.assertNotIn(" impact `IMP-", topic_text)
        self.assertNotIn(": confirmed", topic_text)
        self.assertNotIn(": kept:", topic_text)

    def test_requirement_narrative_refresh_replaces_obsolete_current_detail(self) -> None:
        project = self.make_project("requirement-current-state", git=False)
        topic = self.bootstrap_topic(project)
        ledger_revision = topic_revision = 1
        _, ledger_revision, topic_revision = self.complete_update(
            project,
            topic,
            ledger_revision=ledger_revision,
            topic_revision=topic_revision,
            mutation={
                "type": "refresh-requirement-narrative",
                "goal": "Support offline editing.",
                "background": ["Users work without a network."],
                "scope": ["Offline writes"],
                "non_goals": [],
                "scenarios": ["Edit while disconnected"],
                "tentative_assumptions": ["Conflicts are rare."],
                "facts": ["The client has local storage."],
                "constraints": ["Changes must sync later."],
                "acceptance_conditions": ["Offline edits survive restart."],
                "direction_change_summary": [],
            },
        )
        _, ledger_revision, topic_revision = self.complete_update(
            project,
            topic,
            ledger_revision=ledger_revision,
            topic_revision=topic_revision,
            mutation={
                "type": "refresh-requirement-narrative",
                "goal": "Support online editing only.",
                "background": ["The first release requires a network."],
                "scope": ["Online writes"],
                "non_goals": ["Offline editing"],
                "scenarios": ["Edit while connected"],
                "tentative_assumptions": [],
                "facts": ["The server is authoritative."],
                "constraints": ["Reject writes while disconnected."],
                "acceptance_conditions": ["Disconnected writes fail clearly."],
                "direction_change_summary": [
                    "Offline editing was removed because it is outside the first release."
                ],
            },
        )
        text = Path(str(topic["topic_document_path"])).read_text(encoding="utf-8")
        self.assertIn("Support online editing only.", text)
        self.assertIn("Disconnected writes fail clearly.", text)
        self.assertIn("Offline editing was removed because", text)
        self.assertNotIn("Support offline editing.", text)
        self.assertNotIn("Offline edits survive restart.", text)
        code, current, stderr = self.run_cli(
            self.evolution_request(topic, operation="read-topic")
        )
        self.assertEqual(code, 0, stderr)
        self.assertEqual(
            current["requirement_narrative"]["goal"],
            "Support online editing only.",
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
            self.checkpoint_request(topic, operation="read-topic")
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
                    )
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
                    )
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
                ledger_revision=3,
                checkpoint_id=prepared["checkpoint_id"],
                expected_checkpoint_revision=2,
                broken_identity=commit_id,
                reason="history rewritten",
            )
        )
        self.assertEqual(returncode, 0, stderr)
        self.assertTrue(broken["original_fact_preserved"])
        returncode, injected, _ = self.run_cli(
            self.checkpoint_request(
                topic,
                operation="repair-checkpoint",
                ledger_revision=4,
                checkpoint_id=prepared["checkpoint_id"],
                expected_checkpoint_revision=3,
                replacement_commit=replacement_commit,
                replacement_base_ref=rewritten_parent,
            ),
            failpoint="repair-before-result-record",
        )
        self.assertEqual(returncode, 1)
        self.assertEqual(injected["error"]["code"], "injected_failure")
        returncode, repaired, stderr = self.run_cli(
            self.checkpoint_request(
                topic,
                operation="repair-checkpoint",
                ledger_revision=4,
                checkpoint_id=prepared["checkpoint_id"],
                expected_checkpoint_revision=3,
                replacement_commit=replacement_commit,
                replacement_base_ref=rewritten_parent,
            )
        )
        self.assertEqual(returncode, 0, stderr)
        self.assertEqual(repaired["broken_identity"], commit_id)
        self.assertEqual(repaired["replacement_identity"], replacement_commit)
        self.assertNotEqual(repaired["replacement_identity"], commit_id)
        self.assertTrue(repaired["original_fact_preserved"])
        returncode, validated, stderr = self.run_cli(
            self.checkpoint_request(topic, operation="read-topic")
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
            self.checkpoint_request(topic, operation="read-topic")
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
        publish_request = self.checkpoint_request(
            topic,
            operation="publish-git-checkpoint",
            ledger_revision=4,
            checkpoint_id=prepared["checkpoint_id"],
            expected_checkpoint_revision=1,
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
        self.assertEqual(snapshot_path.stat().st_nlink, 1)
        self.assertEqual(
            [path.name for path in snapshot_path.parent.iterdir() if path.name.startswith(".")],
            [],
        )
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

    def test_non_git_snapshot_publication_rejects_symlinked_object_parent(self) -> None:
        project = self.make_project("snapshot-symlinked-parent", git=False)
        topic = self.bootstrap_topic(project)
        prepared = self.prepare_checkpoint(
            topic,
            ledger_revision=1,
            base_ref="project-root",
        )
        ledger_path = Path(str(topic["ledger_path"]))
        ledger_before = ledger_path.read_bytes()
        external_root = self.root / "external-snapshots"
        external_root.mkdir()
        snapshot_root = project / ".codex" / "design-discussion" / "v1" / "checkpoints"
        snapshot_root.symlink_to(external_root, target_is_directory=True)

        returncode, rejected, _ = self.run_cli(
            self.checkpoint_request(
                topic,
                operation="publish-non-git-checkpoint",
                ledger_revision=2,
                checkpoint_id=prepared["checkpoint_id"],
                expected_checkpoint_revision=1,
            )
        )

        self.assertEqual(returncode, 1)
        self.assertEqual(rejected["error"]["code"], "invalid_storage_path")
        self.assertEqual(ledger_path.read_bytes(), ledger_before)
        self.assertEqual(list(external_root.iterdir()), [])

    def test_non_git_snapshot_publication_rejects_ancestor_swap_before_create(
        self,
    ) -> None:
        project = self.make_project("snapshot-ancestor-swap", git=False)
        topic = self.bootstrap_topic(project)
        prepared = self.prepare_checkpoint(
            topic,
            ledger_revision=1,
            base_ref="project-root",
        )
        request = self.checkpoint_request(
            topic,
            operation="publish-non-git-checkpoint",
            ledger_revision=2,
            checkpoint_id=prepared["checkpoint_id"],
            expected_checkpoint_revision=1,
        )
        ledger_path = Path(str(topic["ledger_path"]))
        ledger_before = ledger_path.read_bytes()
        coordination_parent = project / ".codex"
        retained_parent = project / ".codex-retained"
        external_root = self.root / "external-ancestor-swap"
        external_root.mkdir()

        def swap_coordination_parent(name: str) -> None:
            if name != "snapshot-before-create":
                return
            coordination_parent.rename(retained_parent)
            coordination_parent.symlink_to(external_root, target_is_directory=True)

        try:
            with mock.patch(
                "discussion_core.checkpoints._inject_failure",
                side_effect=swap_coordination_parent,
            ):
                with self.assertRaises(PROTOCOL.ProtocolError) as raised:
                    PROTOCOL._publish_non_git_checkpoint(request)
        finally:
            if coordination_parent.is_symlink():
                coordination_parent.unlink()
            if retained_parent.exists():
                retained_parent.rename(coordination_parent)

        self.assertEqual(raised.exception.code, "invalid_storage_path")
        self.assertEqual(ledger_path.read_bytes(), ledger_before)
        self.assertEqual(list(external_root.iterdir()), [])


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
        for handoff_kind in ("child", "continuation"):
            blocked.append(
                self.handoff_request(
                    topic,
                    operation="prepare-handoff",
                    ledger_revision=2,
                    handoff_kind=handoff_kind,
                    target_slug=f"closed-{handoff_kind}",
                    scope=["gate"],
                    work_snapshot={
                        "goal": "Attempt gated substantive work.",
                        "confirmed_decisions": [],
                        "pending_questions": ["Is the authority current?"],
                    },
                    authoritative_references=[
                        {"kind": "checkpoint", "identity": "CP-source", "sha256": "1" * 64}
                    ],
                )
            )
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
                    "record_revision_conflict" if fault == "broken" else "topic_gate_evaluation_stale",
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
                    "ledger_revision_conflict" if fault == "broken" else "topic_gate_evaluation_stale",
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

    def test_ticket07_closed_gate_blocks_phase1_no_code_integration_via_cli(self) -> None:
        project = self.make_project("ticket07-no-code-gate", git=False)
        topic = self.bootstrap_topic(project)
        ledger = Path(str(topic["ledger_path"]))
        frontmatter, records = PROTOCOL._load_records(ledger)
        records["Current Topics"][0]["current_phase"] = 1
        prerequisite_id = "topic-" + uuid.uuid4().hex
        dependency_id = "DEP-" + uuid.uuid4().hex
        records["Current Topics"].append({"topic_id": prerequisite_id, "record_revision": 1, "root_slug": "prerequisite", "parent_topic_id": None, "current_phase": 0, "phase_state": "active", "review_state": "unreviewed", "topic_state": "open", "topic_document_path": None})
        records["Conversation Bindings"].append({"topic_id": prerequisite_id, "conversation_ref": "codex-thread:prerequisite", "binding_state": "active", "record_revision": 1, "handoff_id": None, "attempt_id": None})
        records["Topic Dependencies"].append({"dependency_id": dependency_id, "record_revision": 1, "dependent_topic_id": topic["topic_id"], "prerequisite_topic_id": prerequisite_id, "requirement_kind": "confirmed-decision", "requirement_summary": "The prerequisite authority is required.", "relation_state": "active", "gate_state": "closed", "accepted_basis_json": None, "gate_reason_json": PROTOCOL._canonical_json({"kind": "explicit-create", "dependency_update_id": "00000000-0000-4000-8000-000000000000", "ledger_revision": 1})})
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
