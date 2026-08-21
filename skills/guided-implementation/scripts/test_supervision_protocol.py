#!/usr/bin/env python3

from __future__ import annotations

import importlib.util
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
import uuid
from unittest import mock


SCRIPT_PATH = Path(__file__).with_name("supervision_protocol.py")
DISCUSSION_SCRIPT_PATH = (
    Path(__file__).parents[2]
    / "design-discussion"
    / "scripts"
    / "discussion_protocol.py"
)
SPEC = importlib.util.spec_from_file_location("supervision_protocol", SCRIPT_PATH)
assert SPEC is not None and SPEC.loader is not None
PROTOCOL = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(PROTOCOL)


class DocumentLeaseTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary_directory.name).resolve()
        self.repository = self.root / "repository"
        subprocess.run(
            ["git", "init", "-q", str(self.repository)],
            check=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )

    def tearDown(self) -> None:
        self.temporary_directory.cleanup()

    def write_input(
        self,
        name: str,
        *,
        task_id: str,
        ttl_seconds: int = 300,
        stage: str = "problem-framing",
        purpose: str = "document-write",
    ) -> Path:
        path = self.root / name
        path.write_text(
            json.dumps(
                {
                    "owner_host_id": "host-1",
                    "owner_task_id": task_id,
                    "purpose": purpose,
                    "repository": str(self.repository),
                    "stage": stage,
                    "ttl_seconds": ttl_seconds,
                },
                sort_keys=True,
                separators=(",", ":"),
            )
            + "\n",
            encoding="utf-8",
        )
        return path

    def write_repository_lease_input(self, name: str) -> Path:
        path = self.root / name
        path.write_text(
            json.dumps(
                {
                    "base_branch": "main",
                    "base_head": "0" * 40,
                    "owner_host_id": "host-1",
                    "owner_task_id": "task-a",
                    "repository": str(self.repository),
                },
                sort_keys=True,
                separators=(",", ":"),
            )
            + "\n",
            encoding="utf-8",
        )
        return path

    def write_repository_coordination_input(self, name: str) -> Path:
        path = self.root / name
        path.write_text(
            json.dumps(
                {
                    "owner_host_id": "host-1",
                    "owner_task_id": "discussion-task",
                    "purpose": "checkpoint-publish",
                    "repository": str(self.repository),
                    "stage": "design-discussion",
                    "ttl_seconds": 300,
                },
                sort_keys=True,
                separators=(",", ":"),
            )
            + "\n",
            encoding="utf-8",
        )
        return path

    def discussion_cli(self, request: dict[str, object]) -> dict[str, object]:
        completed = subprocess.run(
            [sys.executable, str(DISCUSSION_SCRIPT_PATH)],
            input=json.dumps(request),
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        self.assertEqual(completed.returncode, 0, completed.stderr)
        return json.loads(completed.stdout)

    def discussion_request(
        self,
        topic: dict[str, object],
        operation: str,
        *,
        expected_revision: int | None = None,
        **parameters: object,
    ) -> dict[str, object]:
        request: dict[str, object] = {
            "protocol_version": 1,
            "operation": operation,
            "project_path": str(self.repository),
            "project_id": topic["project_id"],
            "tree_id": topic["tree_id"],
            "actor_topic_id": topic["topic_id"],
            "actor_conversation_ref": "discussion-task",
        }
        if expected_revision is not None:
            request.update(
                {
                    "expected_ledger_revision": expected_revision,
                    "expected_topic_revision": 1,
                    "idempotency_key": str(uuid.uuid4()),
                }
            )
        request.update(parameters)
        return request

    def bootstrap_discussion_repository(self) -> tuple[dict[str, object], str]:
        topic = self.discussion_cli(
            {
                "protocol_version": 1,
                "operation": "bootstrap",
                "project_path": str(self.repository),
                "entry_mode": "explicit-skill",
                "conversation_ref": "discussion-task",
                "idempotency_key": str(uuid.uuid4()),
                "root_slug": "isolated-worktree",
            }
        )
        (self.repository / "base.txt").write_text("base\n", encoding="utf-8")
        subprocess.run(["git", "-C", str(self.repository), "add", "base.txt", "docs"], check=True)
        subprocess.run(
            ["git", "-C", str(self.repository), "-c", "user.name=Test", "-c", "user.email=test@example.com", "commit", "-qm", "base"],
            check=True,
        )
        base = subprocess.run(
            ["git", "-C", str(self.repository), "rev-parse", "HEAD"],
            check=True,
            stdout=subprocess.PIPE,
            text=True,
        ).stdout.strip()
        return topic, base

    def create_coordinated_worktree_lease(
        self,
        topic: dict[str, object],
        *,
        base: str,
        implementation_id: str,
        branch: str,
        worktree: Path,
        ledger_revision: int,
        fault_after_git: bool = False,
        exercise_invalid_decision: bool = False,
    ) -> dict[str, object]:
        scope = {
            "paths": [f"src/{implementation_id}.py"],
            "modules": [implementation_id],
            "interfaces": [implementation_id],
            "database_objects": [],
            "dependencies": [],
            "base_commit": base,
            "branch": branch,
            "worktree_path": str(worktree),
        }
        self.discussion_cli(
            self.discussion_request(
                topic,
                "prepare-implementation-run",
                expected_revision=ledger_revision,
                implementation_id=implementation_id,
                scope=scope,
            )
        )
        state_input = self.root / f"state-{implementation_id}.json"
        state_input.write_text(
            json.dumps(
                {
                    "project_id": topic["project_id"],
                    "repository": str(self.repository),
                    "topic_id": topic["topic_id"],
                    "tree_id": topic["tree_id"],
                },
                sort_keys=True,
                separators=(",", ":"),
            )
            + "\n",
            encoding="utf-8",
        )
        state = json.loads(
            subprocess.run(
                [sys.executable, str(SCRIPT_PATH), "create-worktree-state-receipt", "--input", str(state_input)],
                check=True,
                stdout=subprocess.PIPE,
                text=True,
            ).stdout
        )
        state_receipt = {key: state[key] for key in ("file_bytes", "file_sha256", "path", "version")}
        checked = self.discussion_cli(
            self.discussion_request(
                topic,
                "check-implementation-parallelism",
                expected_revision=ledger_revision + 1,
                implementation_id=implementation_id,
                worktree_receipt=state_receipt,
            )
        )
        self.assertEqual(checked["verdict"], "safe")
        coordination_input = self.root / f"coordination-{implementation_id}.json"
        coordination_input.write_text(
            json.dumps(
                {
                    "owner_host_id": "host-1",
                    "owner_task_id": implementation_id,
                    "purpose": "worktree-creation",
                    "repository": str(self.repository),
                    "stage": "guided-implementation",
                    "ttl_seconds": 300,
                },
                sort_keys=True,
                separators=(",", ":"),
            )
            + "\n",
            encoding="utf-8",
        )
        coordination = json.loads(
            subprocess.run(
                [sys.executable, str(SCRIPT_PATH), "acquire-repository-coordination-lease", "--input", str(coordination_input)],
                check=True,
                stdout=subprocess.PIPE,
                text=True,
            ).stdout
        )
        coordination_receipt = {
            "lease_id": coordination["holder"]["lease_id"],
            "path": coordination["path"],
            "version": coordination["version"],
        }
        phase_run_id = "PR-" + hashlib.sha256(implementation_id.encode()).hexdigest()[:16]
        binding = {
            "base_commit": base,
            "implementation_branch": branch,
            "implementation_id": implementation_id,
            "phase_run_id": phase_run_id,
            "repository": str(self.repository),
            "scope_sha256": hashlib.sha256(json.dumps(scope, sort_keys=True, separators=(",", ":")).encode()).hexdigest(),
            "sensitive_shared_surfaces": ["git-common-dir"],
            "topic_id": topic["topic_id"],
            "worktree_path": str(worktree),
        }
        decision_file = self.root / f"decision-{implementation_id}.json"
        if exercise_invalid_decision:
            decision_file.write_text("任意非空确认文本。\n", encoding="utf-8")
            invalid_confirmation_input = self.root / f"invalid-confirmation-{implementation_id}.json"
            invalid_confirmation_input.write_text(
                json.dumps(
                    {
                        "binding": binding,
                        "parallelism_validation": self.discussion_request(
                            topic,
                            "validate-implementation-parallelism",
                            implementation_id=implementation_id,
                            receipt=checked["receipt"],
                            worktree_receipt=state_receipt,
                        ),
                        "source_task_id": implementation_id,
                        "user_decision_file": str(decision_file),
                    },
                    sort_keys=True,
                    separators=(",", ":"),
                )
                + "\n",
                encoding="utf-8",
            )
            rejected = subprocess.run(
                [sys.executable, str(SCRIPT_PATH), "record-isolated-worktree-confirmation", "--input", str(invalid_confirmation_input)],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
            )
            self.assertEqual(rejected.returncode, 2)
            self.assertEqual(
                json.loads(rejected.stdout)["error"]["code"],
                "isolated_confirmation_required",
            )
        decision_file.write_text(
            json.dumps(
                {
                    **binding,
                    "confirmed": True,
                    "creation_action": "create-isolated-worktree",
                    "schema": "isolated-worktree-user-decision-v1",
                    "source_task_id": implementation_id,
                },
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            )
            + "\n",
            encoding="utf-8",
        )
        confirmation_input = self.root / f"confirmation-{implementation_id}.json"
        confirmation_input.write_text(
            json.dumps(
                {
                    "binding": binding,
                    "parallelism_validation": self.discussion_request(
                        topic,
                        "validate-implementation-parallelism",
                        implementation_id=implementation_id,
                        receipt=checked["receipt"],
                        worktree_receipt=state_receipt,
                    ),
                    "source_task_id": implementation_id,
                    "user_decision_file": str(decision_file),
                },
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            )
            + "\n",
            encoding="utf-8",
        )
        confirmation = json.loads(
            subprocess.run(
                [sys.executable, str(SCRIPT_PATH), "record-isolated-worktree-confirmation", "--input", str(confirmation_input)],
                check=True,
                stdout=subprocess.PIPE,
                text=True,
            ).stdout
        )
        confirmation_receipt = {key: confirmation[key] for key in ("file_bytes", "file_sha256", "path", "version")}
        creation_input = self.root / f"creation-{implementation_id}.json"
        creation_input.write_text(
            json.dumps(
                {
                    "confirmation": confirmation_receipt,
                    "coordination_lease": coordination_receipt,
                    "repository": str(self.repository),
                },
                sort_keys=True,
                separators=(",", ":"),
            )
            + "\n",
            encoding="utf-8",
        )
        environment = dict(os.environ)
        if fault_after_git:
            environment["CODEX_SUPERVISION_TEST_FAILPOINT"] = "create-isolated-after-git"
        creation = subprocess.run(
            [sys.executable, str(SCRIPT_PATH), "create-isolated-worktree", "--input", str(creation_input)],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            env=environment,
        )
        if fault_after_git:
            self.assertEqual(creation.returncode, 2)
            unknown = json.loads(creation.stdout)
            self.assertEqual(unknown["error"]["code"], "outcome_unknown")
            current = unknown["current"]
            reconciliation_input = self.root / f"reconcile-{implementation_id}.json"
            reconciliation_input.write_text(
                json.dumps(
                    {
                        "confirmation": {key: current[key] for key in ("file_bytes", "file_sha256", "path", "version")},
                        "coordination_lease": coordination_receipt,
                        "repository": str(self.repository),
                    },
                    sort_keys=True,
                    separators=(",", ":"),
                )
                + "\n",
                encoding="utf-8",
            )
            created = json.loads(
                subprocess.run(
                    [sys.executable, str(SCRIPT_PATH), "reconcile-isolated-worktree-creation", "--input", str(reconciliation_input)],
                    check=True,
                    stdout=subprocess.PIPE,
                    text=True,
                ).stdout
            )
            self.assertTrue(created["reconciled"])
        else:
            self.assertEqual(creation.returncode, 0, creation.stderr)
            created = json.loads(creation.stdout)
        replayed_creation = subprocess.run(
            [sys.executable, str(SCRIPT_PATH), "create-isolated-worktree", "--input", str(creation_input)],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        self.assertEqual(replayed_creation.returncode, 2)
        self.assertEqual(json.loads(replayed_creation.stdout)["error"]["code"], "receipt_cas_mismatch")
        execution_input = self.root / f"execution-{implementation_id}.json"
        execution_input.write_text(
            json.dumps(
                {
                    **binding,
                    "confirmation": {key: created[key] for key in ("file_bytes", "file_sha256", "path", "version")},
                    "coordination_lease": coordination_receipt,
                    "owner_host_id": "host-1",
                    "owner_task_id": implementation_id,
                    "ttl_seconds": 3600,
                },
                sort_keys=True,
                separators=(",", ":"),
            )
            + "\n",
            encoding="utf-8",
        )
        forged_execution = json.loads(execution_input.read_text(encoding="utf-8"))
        forged_execution["confirmation"]["file_sha256"] = "0" * 64
        forged_execution_input = self.root / f"execution-forged-{implementation_id}.json"
        forged_execution_input.write_text(
            json.dumps(forged_execution, sort_keys=True, separators=(",", ":")) + "\n",
            encoding="utf-8",
        )
        forged = subprocess.run(
            [sys.executable, str(SCRIPT_PATH), "acquire-worktree-execution-lease", "--input", str(forged_execution_input)],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        self.assertEqual(forged.returncode, 2)
        self.assertEqual(json.loads(forged.stdout)["error"]["code"], "receipt_cas_mismatch")
        lease = json.loads(
            subprocess.run(
                [sys.executable, str(SCRIPT_PATH), "acquire-worktree-execution-lease", "--input", str(execution_input)],
                check=True,
                stdout=subprocess.PIPE,
                text=True,
            ).stdout
        )
        subprocess.run(
            [
                sys.executable,
                str(SCRIPT_PATH),
                "release-repository-coordination-lease",
                "--file",
                coordination["path"],
                "--id",
                coordination["holder"]["lease_id"],
                "--version",
                str(coordination["version"]),
            ],
            check=True,
            stdout=subprocess.PIPE,
            text=True,
        )
        return lease

    def test_new_repository_lease_uses_v2_and_legacy_v1_still_inspects(self) -> None:
        lease_input = self.write_repository_lease_input("repository-lease.json")
        acquired = PROTOCOL.acquire_repository_lease(lease_input)
        self.assertEqual(acquired["lease_version"], 2)
        self.assertEqual(acquired["mode"], "exclusive-checkout-v2")
        PROTOCOL.release_repository_lease(
            Path(acquired["path"]),
            expected_id=acquired["lease_id"],
            expected_bytes=acquired["file_bytes"],
            expected_sha256=acquired["file_sha256"],
        )

        legacy_id = "1" * 32
        legacy = {
            "base_branch": "main",
            "base_head": "0" * 40,
            "complete": f"REPOSITORY_LEASE_COMPLETE:{legacy_id}",
            "lease_id": legacy_id,
            "lease_version": 1,
            "mode": "exclusive-checkout-v1",
            "owner_host_id": "legacy-host",
            "owner_task_id": "legacy-task",
            "repository": str(self.repository),
        }
        legacy_path = self.repository / ".git" / PROTOCOL.LEASE_FILENAME
        legacy_path.write_bytes(PROTOCOL._canonical_json_bytes(legacy))
        legacy_path.chmod(0o400)
        inspected = PROTOCOL.inspect_repository_lease(self.repository)
        self.assertEqual(inspected["lease_version"], 1)
        self.assertEqual(inspected["mode"], "exclusive-checkout-v1")

    def test_closure_document_receipt_requires_v2_lease_evidence_only(self) -> None:
        common_result = {
            "closure_commit": "a" * 40,
            "documents_updated": ["docs/requirement.md"],
            "verification": ["docs-check: passed"],
        }
        v2_facts = {
            "execution_mode": "exclusive-checkout-v2",
            "repository": str(self.repository),
        }
        with self.assertRaises(PROTOCOL.ProtocolError):
            PROTOCOL._validate_closure_phase_result(
                "documents-committed",
                common_result,
                v2_facts,
                closure_version=PROTOCOL.CLOSURE_VERSION,
            )

        v2_result = {
            **common_result,
            "document_lease": {
                "lease_id": "2" * 32,
                "path": str(
                    self.repository / ".git" / PROTOCOL.DOCUMENT_LEASE_FILENAME
                ),
                "state": "available",
                "version": 4,
            },
            "preserved_documents": ["docs/other-requirement.md"],
        }
        normalized = PROTOCOL._validate_closure_phase_result(
            "documents-committed",
            v2_result,
            v2_facts,
            closure_version=PROTOCOL.CLOSURE_VERSION,
        )
        self.assertEqual(normalized, v2_result)

        legacy_facts = {
            "execution_mode": "exclusive-checkout-v1",
            "repository": str(self.repository),
        }
        legacy_normalized = PROTOCOL._validate_closure_phase_result(
            "documents-committed",
            common_result,
            legacy_facts,
            closure_version=PROTOCOL.CLOSURE_VERSION,
        )
        self.assertEqual(legacy_normalized, common_result)

    def test_acquire_renew_verify_and_release(self) -> None:
        lease_input = self.write_input("lease.json", task_id="task-a")
        with mock.patch.object(PROTOCOL, "_now_epoch", return_value=1_000):
            acquired = PROTOCOL.acquire_document_lease(
                lease_input, wait_seconds=0, max_retries=0
            )
        self.assertTrue(acquired["acquired"])
        self.assertEqual(acquired["version"], 1)
        lease_path = Path(acquired["path"])

        with mock.patch.object(PROTOCOL, "_now_epoch", return_value=1_010):
            verified = PROTOCOL.verify_document_lease(
                lease_path,
                expected_id=acquired["holder"]["lease_id"],
                expected_version=1,
            )
            renewed = PROTOCOL.renew_document_lease(
                lease_path,
                expected_id=acquired["holder"]["lease_id"],
                expected_version=1,
                ttl_seconds=300,
            )
        self.assertTrue(verified["verified"])
        self.assertTrue(renewed["renewed"])
        self.assertEqual(renewed["version"], 2)
        self.assertEqual(renewed["holder"]["expires_at_epoch"], 1_310)

        with mock.patch.object(PROTOCOL, "_now_epoch", return_value=1_020):
            released = PROTOCOL.release_document_lease(
                lease_path,
                expected_id=acquired["holder"]["lease_id"],
                expected_version=2,
            )
        self.assertTrue(released["released"])
        self.assertEqual(released["state"], "available")
        self.assertEqual(released["version"], 3)
        self.assertIsNone(released["holder"])

    def test_repository_coordination_lease_is_separate_and_cas_protected(self) -> None:
        input_path = self.write_repository_coordination_input(
            "repository-coordination.json"
        )
        with mock.patch.object(PROTOCOL, "_now_epoch", return_value=1_000):
            acquired = PROTOCOL.acquire_repository_coordination_lease(input_path)
        self.assertTrue(acquired["acquired"])
        self.assertEqual(acquired["state"], "held")
        self.assertEqual(
            Path(acquired["path"]),
            self.repository / ".git" / PROTOCOL.REPOSITORY_COORDINATION_LEASE_FILENAME,
        )
        self.assertFalse(
            (self.repository / ".git" / PROTOCOL.DOCUMENT_LEASE_FILENAME).exists()
        )
        with mock.patch.object(PROTOCOL, "_now_epoch", return_value=1_010):
            verified = PROTOCOL.verify_repository_coordination_lease(
                Path(acquired["path"]),
                expected_id=acquired["holder"]["lease_id"],
                expected_version=acquired["version"],
            )
            self.assertTrue(verified["verified"])
            released = PROTOCOL.release_repository_coordination_lease(
                Path(acquired["path"]),
                expected_id=acquired["holder"]["lease_id"],
                expected_version=acquired["version"],
            )
        self.assertTrue(released["released"])
        self.assertEqual(released["state"], "available")
        with self.assertRaises(PROTOCOL.ProtocolError):
            PROTOCOL.release_repository_coordination_lease(
                Path(acquired["path"]),
                expected_id=acquired["holder"]["lease_id"],
                expected_version=acquired["version"],
            )

    def test_design_discussion_is_an_accepted_document_lease_stage(self) -> None:
        lease_input = self.write_input(
            "design-discussion-lease.json",
            task_id="discussion-task",
            stage="design-discussion",
        )
        acquired = PROTOCOL.acquire_document_lease(
            lease_input, wait_seconds=0, max_retries=0
        )
        self.assertTrue(acquired["acquired"])
        self.assertEqual(acquired["holder"]["stage"], "design-discussion")
        PROTOCOL.release_document_lease(
            Path(acquired["path"]),
            expected_id=acquired["holder"]["lease_id"],
            expected_version=acquired["version"],
        )

    def test_non_git_project_uses_shared_codex_document_lease(self) -> None:
        non_git_project = self.root / "non-git-project"
        non_git_project.mkdir()
        self.repository = non_git_project
        lease_input = self.write_input(
            "non-git-document-lease.json",
            task_id="discussion-task",
            stage="design-discussion",
        )
        acquired = PROTOCOL.acquire_document_lease(
            lease_input, wait_seconds=0, max_retries=0
        )
        self.assertEqual(
            Path(acquired["path"]),
            non_git_project / ".codex" / PROTOCOL.DOCUMENT_LEASE_FILENAME,
        )
        verified = PROTOCOL.verify_document_lease(
            Path(acquired["path"]),
            expected_id=acquired["holder"]["lease_id"],
            expected_version=acquired["version"],
        )
        self.assertTrue(verified["verified"])
        released = PROTOCOL.release_document_lease(
            Path(acquired["path"]),
            expected_id=acquired["holder"]["lease_id"],
            expected_version=acquired["version"],
        )
        self.assertTrue(released["released"])

    def test_expired_lease_can_be_replaced_but_old_owner_cannot_release(self) -> None:
        first_input = self.write_input("first.json", task_id="task-a", ttl_seconds=10)
        second_input = self.write_input("second.json", task_id="task-b", ttl_seconds=10)
        with mock.patch.object(PROTOCOL, "_now_epoch", return_value=1_000):
            first = PROTOCOL.acquire_document_lease(
                first_input, wait_seconds=0, max_retries=0
            )
        with mock.patch.object(PROTOCOL, "_now_epoch", return_value=1_011):
            second = PROTOCOL.acquire_document_lease(
                second_input, wait_seconds=0, max_retries=0
            )
        self.assertTrue(second["acquired"])
        self.assertEqual(
            second["replaced_expired_lease_id"], first["holder"]["lease_id"]
        )
        self.assertEqual(second["version"], 2)

        with self.assertRaises(PROTOCOL.ProtocolError):
            PROTOCOL.release_document_lease(
                Path(first["path"]),
                expected_id=first["holder"]["lease_id"],
                expected_version=1,
            )

    def test_waits_ten_times_then_returns_timeout(self) -> None:
        first_input = self.write_input("first.json", task_id="task-a", ttl_seconds=1_000)
        second_input = self.write_input("second.json", task_id="task-b", ttl_seconds=1_000)
        with mock.patch.object(PROTOCOL, "_now_epoch", return_value=1_000):
            PROTOCOL.acquire_document_lease(first_input, wait_seconds=0, max_retries=0)

        with (
            mock.patch.object(PROTOCOL, "_now_epoch", return_value=1_001),
            mock.patch.object(PROTOCOL.time, "sleep") as sleep,
        ):
            result = PROTOCOL.acquire_document_lease(
                second_input, wait_seconds=10, max_retries=10
            )
        self.assertEqual(result["state"], "timeout")
        self.assertFalse(result["acquired"])
        self.assertEqual(result["attempts"], 11)
        self.assertEqual(result["waited_seconds"], 100)
        self.assertEqual(sleep.call_count, 10)
        sleep.assert_called_with(10)

    def test_concurrent_cli_acquire_has_one_winner(self) -> None:
        first_input = self.write_input("first.json", task_id="task-a")
        second_input = self.write_input("second.json", task_id="task-b")
        command = [
            sys.executable,
            str(SCRIPT_PATH),
            "acquire-document-lease",
            "--wait-seconds",
            "0",
            "--max-retries",
            "0",
            "--input",
        ]
        first = subprocess.Popen(
            command + [str(first_input)],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        second = subprocess.Popen(
            command + [str(second_input)],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        first_stdout, first_stderr = first.communicate(timeout=10)
        second_stdout, second_stderr = second.communicate(timeout=10)
        self.assertEqual(first.returncode, 0, first_stderr)
        self.assertEqual(second.returncode, 0, second_stderr)
        results = [json.loads(first_stdout), json.loads(second_stdout)]
        self.assertEqual(sum(bool(result["acquired"]) for result in results), 1)
        self.assertEqual(
            sorted(result["state"] for result in results), ["held", "timeout"]
        )

    def test_worktree_execution_lease_cli_binds_exact_worktree_and_v2_queues(self) -> None:
        topic, base = self.bootstrap_discussion_repository()
        worktree = self.root / "isolated-wi07"
        lease = self.create_coordinated_worktree_lease(
            topic,
            base=base,
            implementation_id="implementation-wi07",
            branch="codex/isolated-wi07",
            worktree=worktree,
            ledger_revision=1,
            fault_after_git=True,
        )
        self.assertEqual(lease["state"], "held")
        self.assertEqual(lease["binding"]["worktree_path"], str(worktree))

        verified = subprocess.run(
            [sys.executable, str(SCRIPT_PATH), "verify-worktree-execution-lease", "--file", lease["path"], "--id", lease["holder"]["lease_id"], "--version", str(lease["version"]), "--platform-cwd", str(worktree)],
            check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
        )
        self.assertTrue(json.loads(verified.stdout)["verified"])
        blocked = subprocess.run(
            [sys.executable, str(SCRIPT_PATH), "verify-worktree-execution-lease", "--file", lease["path"], "--id", lease["holder"]["lease_id"], "--version", str(lease["version"]), "--platform-cwd", str(self.repository)],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
        )
        self.assertEqual(blocked.returncode, 2)
        structured = json.loads(blocked.stdout)
        self.assertFalse(structured["ok"])
        self.assertEqual(structured["error"]["code"], "receipt_identity_mismatch")

        second_worktree = self.root / "isolated-wi08"
        second = self.create_coordinated_worktree_lease(
            topic,
            base=base,
            implementation_id="implementation-wi08",
            branch="codex/isolated-wi08",
            worktree=second_worktree,
            ledger_revision=3,
        )
        inspected = json.loads(
            subprocess.run(
                [sys.executable, str(SCRIPT_PATH), "inspect-worktree-execution-leases", "--repository", str(self.repository)],
                check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
            ).stdout
        )
        self.assertEqual(
            {item["binding"]["implementation_id"] for item in inspected["leases"] if item["state"] == "held"},
            {"implementation-wi07", "implementation-wi08"},
        )

        availability_input = self.root / "availability.json"
        availability_input.write_text(
            json.dumps({"execution_mode": "exclusive-checkout-v2", "repository": str(self.repository)}, sort_keys=True, separators=(",", ":")) + "\n",
            encoding="utf-8",
        )
        availability = subprocess.run(
            [sys.executable, str(SCRIPT_PATH), "check-execution-availability", "--input", str(availability_input)],
            check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
        )
        queued = json.loads(availability.stdout)
        self.assertEqual(queued["state"], "queued")
        self.assertIn(lease["holder"]["lease_id"], {item["lease_id"] for item in queued["blockers"]})
        availability_input.write_text(
            json.dumps({"execution_mode": "isolated-worktree-v1", "repository": str(self.repository)}, sort_keys=True, separators=(",", ":")) + "\n",
            encoding="utf-8",
        )
        isolated_availability = json.loads(
            subprocess.run(
                [sys.executable, str(SCRIPT_PATH), "check-execution-availability", "--input", str(availability_input)],
                check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
            ).stdout
        )
        self.assertEqual(isolated_availability["state"], "ready")
        self.assertEqual(len(isolated_availability["active_execution_leases"]), 2)

        released = subprocess.run(
            [sys.executable, str(SCRIPT_PATH), "release-worktree-execution-lease", "--file", lease["path"], "--id", lease["holder"]["lease_id"], "--version", str(lease["version"])],
            check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
        )
        self.assertEqual(json.loads(released.stdout)["state"], "available")
        subprocess.run(
            [sys.executable, str(SCRIPT_PATH), "reconcile-worktree-execution-lease", "--file", second["path"], "--id", second["holder"]["lease_id"], "--version", str(second["version"]), "--platform-cwd", str(second_worktree), "--outcome", "released"],
            check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
        )

        stale_release = subprocess.run(
            [sys.executable, str(SCRIPT_PATH), "release-worktree-execution-lease", "--file", lease["path"], "--id", lease["holder"]["lease_id"], "--version", str(lease["version"])],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
        )
        self.assertEqual(stale_release.returncode, 2)
        self.assertEqual(json.loads(stale_release.stdout)["error"]["code"], "worktree_lease_release_cas_mismatch")

        invalid_availability = self.root / "invalid-availability.json"
        invalid_availability.write_text(
            json.dumps({"execution_mode": "caller-invented", "repository": str(self.repository)}, sort_keys=True, separators=(",", ":")) + "\n",
            encoding="utf-8",
        )
        invalid = subprocess.run(
            [sys.executable, str(SCRIPT_PATH), "check-execution-availability", "--input", str(invalid_availability)],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
        )
        self.assertEqual(invalid.returncode, 2)
        self.assertEqual(json.loads(invalid.stdout)["error"]["code"], "execution_availability_invalid")

        invalid_reconcile = subprocess.run(
            [sys.executable, str(SCRIPT_PATH), "reconcile-worktree-execution-lease", "--file", second["path"], "--id", second["holder"]["lease_id"], "--version", str(second["version"]), "--platform-cwd", str(second_worktree), "--outcome", "caller-invented"],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
        )
        self.assertEqual(invalid_reconcile.returncode, 2)
        self.assertEqual(json.loads(invalid_reconcile.stdout)["error"]["code"], "worktree_reconciliation_invalid_outcome")

    def test_isolated_user_decision_requires_canonical_exact_payload(self) -> None:
        topic, base = self.bootstrap_discussion_repository()
        lease = self.create_coordinated_worktree_lease(
            topic,
            base=base,
            implementation_id="implementation-canonical-decision",
            branch="codex/canonical-decision",
            worktree=self.root / "isolated-canonical-decision",
            ledger_revision=1,
            exercise_invalid_decision=True,
        )
        self.assertEqual(lease["state"], "held")

    def test_serial_integration_revalidates_source_dependencies_and_active_runs(self) -> None:
        lease_input = self.root / "serial-integration-lease.json"
        lease_input.write_text(
            json.dumps(
                {
                    "owner_host_id": "host-1",
                    "owner_task_id": "integration-task",
                    "purpose": "serial-integration",
                    "repository": str(self.repository),
                    "stage": "guided-implementation",
                    "ttl_seconds": 300,
                },
                sort_keys=True, separators=(",", ":"),
            ) + "\n",
            encoding="utf-8",
        )
        acquired = subprocess.run(
            [sys.executable, str(SCRIPT_PATH), "acquire-repository-coordination-lease", "--input", str(lease_input)],
            check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
        )
        lease = json.loads(acquired.stdout)
        holder = lease["holder"]
        legacy_snapshot = {
            "source_identity": "1" * 40,
            "dependency_receipt": "2" * 64,
            "active_implementations_receipt": "3" * 64,
        }
        request_path = self.root / "integration-revalidation.json"
        request_path.write_text(
            json.dumps(
                {
                    "coordination_lease": {"lease_id": holder["lease_id"], "path": lease["path"], "version": lease["version"]},
                    "current": legacy_snapshot,
                    "expected": legacy_snapshot,
                    "repository": str(self.repository),
                },
                sort_keys=True, separators=(",", ":"),
            ) + "\n",
            encoding="utf-8",
        )
        rejected = subprocess.run(
            [sys.executable, str(SCRIPT_PATH), "revalidate-integration", "--input", str(request_path)],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
        )
        self.assertEqual(rejected.returncode, 2)
        report = json.loads(rejected.stdout)
        self.assertFalse(report["ok"])
        self.assertEqual(report["error"]["code"], "discussion_receipt_invalid")
        self.assertIn("self-comparison", report["error"]["message"])

        subprocess.run(
            [sys.executable, str(SCRIPT_PATH), "release-repository-coordination-lease", "--file", lease["path"], "--id", holder["lease_id"], "--version", str(lease["version"])],
            check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
        )
        wrong_lease_input = self.root / "wrong-integration-lease.json"
        wrong_lease_input.write_text(
            json.dumps(
                {
                    "owner_host_id": "host-1",
                    "owner_task_id": "integration-task",
                    "purpose": "worktree-creation",
                    "repository": str(self.repository),
                    "stage": "guided-implementation",
                    "ttl_seconds": 300,
                },
                sort_keys=True,
                separators=(",", ":"),
            )
            + "\n",
            encoding="utf-8",
        )
        wrong_lease = json.loads(
            subprocess.run(
                [sys.executable, str(SCRIPT_PATH), "acquire-repository-coordination-lease", "--input", str(wrong_lease_input)],
                check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
            ).stdout
        )
        request_path.write_text(
            json.dumps(
                {
                    "coordination_lease": {
                        "lease_id": wrong_lease["holder"]["lease_id"],
                        "path": wrong_lease["path"],
                        "version": wrong_lease["version"],
                    },
                    "discussion_validation": {},
                    "repository": str(self.repository),
                },
                sort_keys=True,
                separators=(",", ":"),
            )
            + "\n",
            encoding="utf-8",
        )
        wrong = subprocess.run(
            [sys.executable, str(SCRIPT_PATH), "revalidate-integration", "--input", str(request_path)],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
        )
        self.assertEqual(wrong.returncode, 2)
        self.assertEqual(json.loads(wrong.stdout)["error"]["code"], "integration_lease_invalid")

    def test_dual_mode_handoffs_are_v4_and_legacy_v3_remains_verifiable(self) -> None:
        runtime_root = self.root / "runtime"
        environment = {**os.environ, "CC_SWITCH_RUNTIME_ROOT": str(runtime_root)}
        source = {
            "checkpoint_id": "CP-implementation-source",
            "commit_id": "1" * 40,
            "committed": True,
            "read_only": True,
            "sha256": "2" * 64,
        }
        envelopes = [
            {
                "checkout_path": str(self.repository),
                "execution_mode": "exclusive-checkout-v2",
                "implementation_source_checkpoint": source,
                "repository": str(self.repository),
                "repository_lease": {"lease_id": "3" * 32, "mode": "exclusive-checkout-v2", "path": str(self.repository / ".git" / PROTOCOL.LEASE_FILENAME)},
                "worktree_count": 0,
            },
            {
                "execution_mode": "isolated-worktree-v1",
                "implementation_source_checkpoint": source,
                "isolated_worktree": {
                    "base_commit": "1" * 40,
                    "branch": "codex/isolated",
                    "execution_lease": {"lease_id": "4" * 32, "path": str(self.repository / ".git" / PROTOCOL.WORKTREE_EXECUTION_LEASE_DIRECTORY / ("a" * 64 + ".json")), "version": 1},
                    "parallelism_receipt": "5" * 64,
                    "scope_sha256": "6" * 64,
                    "sensitive_shared_surfaces": ["git-common-dir"],
                    "worktree_path": str(self.root / "isolated"),
                },
                "repository": str(self.repository),
            },
        ]
        reports = []
        for index, envelope in enumerate(envelopes):
            input_path = self.root / f"handoff-{index}.json"
            input_path.write_text(
                json.dumps({"artifacts": [], "envelope": envelope, "work_items": [{"id": "WI07"}]}, sort_keys=True, separators=(",", ":")) + "\n",
                encoding="utf-8",
            )
            created = subprocess.run(
                [sys.executable, str(SCRIPT_PATH), "create-handoff", "--input", str(input_path)],
                check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, env=environment,
            )
            report = json.loads(created.stdout)
            verified = subprocess.run(
                [sys.executable, str(SCRIPT_PATH), "verify-handoff", "--file", report["path"], "--id", Path(report["path"]).parent.name, "--bytes", str(report["file_bytes"]), "--sha256", report["file_sha256"]],
                check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, env=environment,
            )
            decoded = json.loads(verified.stdout)
            self.assertEqual(decoded["handoff_version"], 4)
            self.assertEqual(decoded["envelope"]["execution_mode"], envelope["execution_mode"])
            reports.append(report)

        legacy_path = Path(reports[0]["path"])
        legacy = json.loads(legacy_path.read_text(encoding="utf-8"))
        legacy["handoff_version"] = 3
        legacy_bytes = json.dumps(legacy, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode() + b"\n"
        legacy_path.chmod(0o600)
        legacy_path.write_bytes(legacy_bytes)
        legacy_path.chmod(0o400)
        verified_legacy = subprocess.run(
            [sys.executable, str(SCRIPT_PATH), "verify-handoff", "--file", str(legacy_path), "--id", legacy_path.parent.name, "--bytes", str(len(legacy_bytes)), "--sha256", hashlib.sha256(legacy_bytes).hexdigest()],
            check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, env=environment,
        )
        self.assertEqual(json.loads(verified_legacy.stdout)["handoff_version"], 3)


class ArchiveIntegrationProtocolTests(DocumentLeaseTests):
    def run_archive_cli(self, command: str, input_path: Path) -> tuple[int, dict[str, object]]:
        completed = subprocess.run(
            [sys.executable, str(SCRIPT_PATH), command, "--input", str(input_path)],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        return completed.returncode, json.loads(completed.stdout)

    def acquire_archive_document_lease(self) -> dict[str, object]:
        lease_input = self.write_input(
            "archive-document-lease.json",
            task_id="archive-task",
            stage="change-closure",
        )
        completed = subprocess.run(
            [sys.executable, str(SCRIPT_PATH), "acquire-document-lease", "--input", str(lease_input)],
            check=True,
            stdout=subprocess.PIPE,
            text=True,
        )
        return json.loads(completed.stdout)

    def proposal_input(
        self,
        *,
        target: Path,
        proposal: Path,
        base: bytes,
        lease: dict[str, object],
    ) -> Path:
        input_path = self.root / f"proposal-{uuid.uuid4().hex}.json"
        input_path.write_text(
            json.dumps(
                {
                    "base_sha256": hashlib.sha256(base).hexdigest(),
                    "document_lease": {
                        "lease_id": lease["holder"]["lease_id"],
                        "path": lease["path"],
                        "version": lease["version"],
                    },
                    "proposal_path": str(proposal),
                    "proposal_sha256": hashlib.sha256(proposal.read_bytes()).hexdigest(),
                    "repository": str(self.repository),
                    "target_path": str(target.relative_to(self.repository)),
                },
                sort_keys=True,
                separators=(",", ":"),
            )
            + "\n",
            encoding="utf-8",
        )
        return input_path

    def test_archive_document_proposal_has_apply_noop_conflict_three_way_semantics(self) -> None:
        target = self.repository / "docs" / "result.md"
        target.parent.mkdir()
        base = b"base\n"
        proposed = b"proposed\n"
        target.write_bytes(base)
        proposal = self.root / "proposal.md"
        proposal.write_bytes(proposed)
        lease = self.acquire_archive_document_lease()

        code, applied = self.run_archive_cli(
            "converge-document-proposal",
            self.proposal_input(target=target, proposal=proposal, base=base, lease=lease),
        )
        self.assertEqual(code, 0, applied)
        self.assertEqual(applied["outcome"], "applied")
        self.assertEqual(target.read_bytes(), proposed)

        code, noop = self.run_archive_cli(
            "converge-document-proposal",
            self.proposal_input(target=target, proposal=proposal, base=base, lease=lease),
        )
        self.assertEqual(code, 0)
        self.assertEqual(noop["outcome"], "no-op")

        target.write_bytes(b"independent-base-change\n")
        code, conflict = self.run_archive_cli(
            "converge-document-proposal",
            self.proposal_input(target=target, proposal=proposal, base=base, lease=lease),
        )
        self.assertEqual(code, 2)
        self.assertEqual(conflict["error"]["code"], "document_proposal_conflict")
        self.assertEqual(target.read_bytes(), b"independent-base-change\n")

    def test_archive_document_proposal_rejects_worktree_as_apply_checkout(self) -> None:
        target = self.repository / "docs" / "result.md"
        target.parent.mkdir()
        target.write_bytes(b"base\n")
        proposal = self.root / "proposal.md"
        proposal.write_bytes(b"proposed\n")
        lease = self.acquire_archive_document_lease()
        source = json.loads(
            self.proposal_input(target=target, proposal=proposal, base=b"base\n", lease=lease).read_text()
        )
        source["repository"] = str(self.root / "implementation-worktree")
        forged = self.root / "forged-proposal.json"
        forged.write_text(json.dumps(source, sort_keys=True, separators=(",", ":")) + "\n")
        code, response = self.run_archive_cli("converge-document-proposal", forged)
        self.assertEqual(code, 2)
        self.assertEqual(response["error"]["code"], "document_lease_invalid")

    def test_closure_protocol_dispatch_is_immutable_for_all_execution_modes(self) -> None:
        self.assertEqual(
            PROTOCOL._closure_phases(PROTOCOL.LEGACY_CLOSURE_VERSION),
            PROTOCOL.LEGACY_CLOSURE_PHASES,
        )
        self.assertEqual(
            PROTOCOL._closure_phases(PROTOCOL.CLOSURE_VERSION),
            PROTOCOL.CLOSURE_PHASES,
        )
        self.assertEqual(
            PROTOCOL._closure_phases(PROTOCOL.ISOLATED_CLOSURE_VERSION),
            PROTOCOL.ISOLATED_CLOSURE_PHASES,
        )
        lease_id = "3" * 32
        facts = PROTOCOL._validate_closure_facts(
            {
                "base_branch": "main",
                "checkout_path": str(self.repository),
                "documentation_proposals": ["docs/result.md"],
                "execution_lease": {
                    "lease_id": lease_id,
                    "path": str(
                        self.repository
                        / ".git"
                        / PROTOCOL.WORKTREE_EXECUTION_LEASE_DIRECTORY
                        / f"{lease_id}.json"
                    ),
                    "version": 7,
                },
                "execution_mode": "isolated-worktree-v1",
                "implementation_branch": "codex/isolated",
                "implementation_commit": "a" * 40,
                "managed_links": [],
                "merge_commit": "b" * 40,
                "remote_actions": [],
                "repository": str(self.repository),
                "source_host_id": "host",
                "source_task_id": "task",
                "spec_references": [],
                "ticket_references": [],
                "worktree_path": str(self.root / "isolated-worktree"),
            },
            "facts",
            closure_version=PROTOCOL.ISOLATED_CLOSURE_VERSION,
        )
        self.assertEqual(facts["execution_mode"], "isolated-worktree-v1")
        with self.assertRaises(PROTOCOL.ProtocolError):
            PROTOCOL._validate_closure_phase_result(
                "worktree-removed",
                {"path": facts["worktree_path"], "verified_absent": True},
                facts,
                closure_version=PROTOCOL.ISOLATED_CLOSURE_VERSION,
            )
        removed = PROTOCOL._validate_closure_phase_result(
            "worktree-removed",
            {
                "observed_before": "present-clean",
                "path": facts["worktree_path"],
                "verified_absent": True,
            },
            facts,
            closure_version=PROTOCOL.ISOLATED_CLOSURE_VERSION,
        )
        self.assertEqual(removed["observed_before"], "present-clean")
        released = PROTOCOL._validate_closure_phase_result(
            "execution-lease-released",
            {
                "lease_id": lease_id,
                "path": facts["execution_lease"]["path"],
                "state": "available",
                "version": 8,
            },
            facts,
            closure_version=PROTOCOL.ISOLATED_CLOSURE_VERSION,
        )
        self.assertEqual(released["state"], "available")

    def test_isolated_cleanup_faults_are_explicit_and_resume_from_first_missing_receipt(self) -> None:
        runtime = self.root / "cc-runtime"
        environment = {**os.environ, "CC_SWITCH_RUNTIME_ROOT": str(runtime)}
        source = {
            "checkpoint_id": "CP-implementation-source",
            "commit_id": "1" * 40,
            "committed": True,
            "read_only": True,
            "sha256": "2" * 64,
        }
        lease_id = "4" * 32
        worktree = self.root / "isolated-worktree"
        handoff_input = self.root / "archive-handoff.json"
        handoff_input.write_text(
            json.dumps(
                {
                    "artifacts": [],
                    "envelope": {
                        "execution_mode": "isolated-worktree-v1",
                        "implementation_source_checkpoint": source,
                        "isolated_worktree": {
                            "base_commit": "1" * 40,
                            "branch": "codex/isolated",
                            "execution_lease": {
                                "lease_id": lease_id,
                                "path": str(self.repository / ".git" / PROTOCOL.WORKTREE_EXECUTION_LEASE_DIRECTORY / "implementation.json"),
                                "version": 7,
                            },
                            "parallelism_receipt": "5" * 64,
                            "scope_sha256": "6" * 64,
                            "sensitive_shared_surfaces": ["git-common-dir"],
                            "worktree_path": str(worktree),
                        },
                        "repository": str(self.repository),
                    },
                    "work_items": [{"id": "WI08"}],
                },
                sort_keys=True,
                separators=(",", ":"),
            )
            + "\n"
        )
        created = subprocess.run(
            [sys.executable, str(SCRIPT_PATH), "create-handoff", "--input", str(handoff_input)],
            check=True, stdout=subprocess.PIPE, text=True, env=environment,
        )
        handoff = json.loads(created.stdout)
        handoff_id = Path(handoff["path"]).parent.name
        payload = self.root / "payload.txt"
        payload.write_text("archive evidence\n")
        published = subprocess.run(
            [sys.executable, str(SCRIPT_PATH), "publish-supervision", "--handoff-file", handoff["path"], "--direction", "parent-to-child", "--kind", "archive-evidence", "--payload-file", str(payload)],
            check=True, stdout=subprocess.PIPE, text=True, env=environment,
        )
        supervision = json.loads(published.stdout)
        cleanup = self.root / "cleanup.json"
        cleanup.write_text(
            json.dumps(
                {
                    "cleanup_version": 1,
                    "handoff_file": {
                        "complete": f"HANDOFF_COMPLETE:{handoff_id}",
                        "file_bytes": handoff["file_bytes"],
                        "file_sha256": handoff["file_sha256"],
                        "path": handoff["path"],
                    },
                    "handoff_id": handoff_id,
                    "manifest_file": supervision["manifest_file"],
                    "supervision_file_count": supervision["supervision_file_count"],
                },
                sort_keys=True,
                separators=(",", ":"),
            )
            + "\n"
        )
        facts = {
            "base_branch": "main",
            "checkout_path": str(self.repository),
            "documentation_proposals": ["docs/result.md"],
            "execution_lease": {
                "lease_id": lease_id,
                "path": str(self.repository / ".git" / PROTOCOL.WORKTREE_EXECUTION_LEASE_DIRECTORY / "implementation.json"),
                "version": 7,
            },
            "execution_mode": "isolated-worktree-v1",
            "implementation_branch": "codex/isolated",
            "implementation_commit": "a" * 40,
            "managed_links": [],
            "merge_commit": "b" * 40,
            "remote_actions": ["push:origin"],
            "repository": str(self.repository),
            "source_host_id": "host",
            "source_task_id": "task",
            "spec_references": [],
            "ticket_references": [],
            "worktree_path": str(worktree),
        }
        closure_input = self.root / "closure-input.json"
        closure_input.write_text(json.dumps({"cleanup_checkpoint": str(cleanup), "facts": facts}, sort_keys=True, separators=(",", ":")) + "\n")
        closure = json.loads(
            subprocess.run(
                [sys.executable, str(SCRIPT_PATH), "create-closure-checkpoint", "--input", str(closure_input)],
                check=True, stdout=subprocess.PIPE, text=True, env=environment,
            ).stdout
        )

        document_result = self.root / "documents.json"
        document_result.write_text(
            json.dumps(
                {
                    "closure_commit": "c" * 40,
                    "document_lease": {
                        "lease_id": "7" * 32,
                        "path": str(self.repository / ".git" / PROTOCOL.DOCUMENT_LEASE_FILENAME),
                        "state": "available",
                        "version": 2,
                    },
                    "documents_updated": ["docs/result.md"],
                    "preserved_documents": [],
                    "proposal_outcomes": [{"base_sha256": "8" * 64, "outcome": "applied", "path": "docs/result.md", "proposal_sha256": "9" * 64}],
                    "verification": ["proposal applied"],
                },
                sort_keys=True,
                separators=(",", ":"),
            )
            + "\n"
        )
        subprocess.run(
            [sys.executable, str(SCRIPT_PATH), "advance-closure-checkpoint", "--checkpoint", closure["path"], "--phase", "documents-committed", "--result", str(document_result)],
            check=True, stdout=subprocess.PIPE, text=True, env=environment,
        )

        def advance_fault(phase: str, result: dict[str, object]) -> dict[str, object]:
            path = self.root / f"{phase}-{uuid.uuid4().hex}.json"
            path.write_text(json.dumps(result, sort_keys=True, separators=(",", ":")) + "\n")
            completed = subprocess.run(
                [sys.executable, str(SCRIPT_PATH), "advance-closure-checkpoint", "--checkpoint", closure["path"], "--phase", phase, "--result", str(path)],
                stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, env=environment,
            )
            return {"code": completed.returncode, "response": json.loads(completed.stdout), "path": path}

        missing = advance_fault("worktree-removed", {"observed_before": "missing", "path": str(worktree), "verified_absent": True})
        self.assertEqual(missing["code"], 2)
        self.assertEqual(json.loads(subprocess.run([sys.executable, str(SCRIPT_PATH), "inspect-closure-checkpoint", "--checkpoint", closure["path"]], check=True, stdout=subprocess.PIPE, text=True, env=environment).stdout)["phase"], "documents-committed")
        self.assertEqual(advance_fault("worktree-removed", {"observed_before": "present-clean", "path": str(worktree), "verified_absent": True})["code"], 0)
        refusal = advance_fault("branch-removed", {"name": "codex/isolated", "observed_before": "present-unmerged", "verified_absent": False})
        self.assertEqual(refusal["code"], 2)
        self.assertEqual(advance_fault("branch-removed", {"name": "codex/isolated", "observed_before": "present-merged", "verified_absent": True})["code"], 0)
        uncertain = advance_fault("execution-lease-released", {"lease_id": lease_id, "path": facts["execution_lease"]["path"], "state": "unknown", "version": 8})
        self.assertEqual(uncertain["code"], 2)
        self.assertEqual(advance_fault("execution-lease-released", {"lease_id": lease_id, "path": facts["execution_lease"]["path"], "state": "available", "version": 8})["code"], 0)
        unbound_remote = advance_fault(
            "remote-verified",
            {"actions": ["push:origin"], "results": [], "verified": True},
        )
        self.assertEqual(unbound_remote["code"], 2)
        self.assertEqual(
            advance_fault(
                "remote-verified",
                {
                    "actions": ["push:origin"],
                    "results": ["push:origin:verified"],
                    "verified": True,
                },
            )["code"],
            0,
        )


if __name__ == "__main__":
    unittest.main()
