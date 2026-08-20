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
from unittest import mock


SCRIPT_PATH = Path(__file__).with_name("supervision_protocol.py")
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
        (self.repository / "base.txt").write_text("base\n", encoding="utf-8")
        subprocess.run(["git", "-C", str(self.repository), "add", "base.txt"], check=True)
        subprocess.run(
            ["git", "-C", str(self.repository), "-c", "user.name=Test", "-c", "user.email=test@example.com", "commit", "-qm", "base"],
            check=True,
        )
        base = subprocess.run(
            ["git", "-C", str(self.repository), "rev-parse", "HEAD"],
            check=True, stdout=subprocess.PIPE, text=True,
        ).stdout.strip()
        worktree = self.root / "isolated-wi07"
        subprocess.run(
            ["git", "-C", str(self.repository), "worktree", "add", "-q", "-b", "codex/isolated-wi07", str(worktree), base],
            check=True,
        )
        lease_input = self.root / "worktree-execution.json"
        lease_input.write_text(
            json.dumps(
                {
                    "base_commit": base,
                    "implementation_branch": "codex/isolated-wi07",
                    "implementation_id": "implementation-wi07",
                    "owner_host_id": "host-1",
                    "owner_task_id": "task-wi07",
                    "phase_run_id": "PR-00000007",
                    "repository": str(self.repository),
                    "scope_sha256": "7" * 64,
                    "sensitive_shared_surfaces": ["git-common-dir"],
                    "topic_id": "topic-" + "7" * 32,
                    "ttl_seconds": 3600,
                    "worktree_path": str(worktree),
                },
                sort_keys=True, separators=(",", ":"),
            ) + "\n",
            encoding="utf-8",
        )
        acquire = subprocess.run(
            [sys.executable, str(SCRIPT_PATH), "acquire-worktree-execution-lease", "--input", str(lease_input)],
            check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
        )
        lease = json.loads(acquire.stdout)
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
        self.assertIn("platform working directory", blocked.stderr)

        second_worktree = self.root / "isolated-wi08"
        subprocess.run(
            ["git", "-C", str(self.repository), "worktree", "add", "-q", "-b", "codex/isolated-wi08", str(second_worktree), base],
            check=True,
        )
        second_input = self.root / "worktree-execution-2.json"
        second_source = json.loads(lease_input.read_text(encoding="utf-8"))
        second_source.update(
            {
                "implementation_branch": "codex/isolated-wi08",
                "implementation_id": "implementation-wi08",
                "owner_task_id": "task-wi08",
                "phase_run_id": "PR-00000008",
                "scope_sha256": "8" * 64,
                "topic_id": "topic-" + "8" * 32,
                "worktree_path": str(second_worktree),
            }
        )
        second_input.write_text(
            json.dumps(second_source, sort_keys=True, separators=(",", ":")) + "\n",
            encoding="utf-8",
        )
        second = json.loads(
            subprocess.run(
                [sys.executable, str(SCRIPT_PATH), "acquire-worktree-execution-lease", "--input", str(second_input)],
                check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
            ).stdout
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
        expected = {
            "source_identity": "1" * 40,
            "dependency_receipt": "2" * 64,
            "active_implementations_receipt": "3" * 64,
        }
        request_path = self.root / "integration-revalidation.json"
        request_path.write_text(
            json.dumps(
                {
                    "coordination_lease": {"lease_id": holder["lease_id"], "path": lease["path"], "version": lease["version"]},
                    "current": expected,
                    "expected": expected,
                    "repository": str(self.repository),
                },
                sort_keys=True, separators=(",", ":"),
            ) + "\n",
            encoding="utf-8",
        )
        ready = subprocess.run(
            [sys.executable, str(SCRIPT_PATH), "revalidate-integration", "--input", str(request_path)],
            check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
        )
        self.assertEqual(json.loads(ready.stdout)["state"], "ready")

        stale = {**expected, "source_identity": "4" * 40, "dependency_receipt": "5" * 64}
        request_path.write_text(
            json.dumps(
                {
                    "coordination_lease": {"lease_id": holder["lease_id"], "path": lease["path"], "version": lease["version"]},
                    "current": stale,
                    "expected": expected,
                    "repository": str(self.repository),
                },
                sort_keys=True, separators=(",", ":"),
            ) + "\n",
            encoding="utf-8",
        )
        blocked = subprocess.run(
            [sys.executable, str(SCRIPT_PATH), "revalidate-integration", "--input", str(request_path)],
            check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
        )
        report = json.loads(blocked.stdout)
        self.assertEqual(report["state"], "blocked")
        self.assertEqual(report["stale_dimensions"], ["source", "dependencies"])

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


if __name__ == "__main__":
    unittest.main()
